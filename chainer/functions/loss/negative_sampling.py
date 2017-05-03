import numpy
import six

from chainer import cuda
from chainer import function
from chainer.utils import type_check


class NegativeSamplingFunction(function.Function):

    ignore_label = -1

    def __init__(self, sampler, sample_size, reduce='sum'):
        self.sampler = sampler
        self.sample_size = sample_size
        self.reduce = reduce

    def _make_samples(self, t):
        if hasattr(self, 'samples'):
            return self.samples  # for testing

        size = int(t.shape[0])
        # first one is the positive, and others are sampled negatives
        samples = self.sampler((size, self.sample_size + 1))
        samples[:, 0] = t
        self.samples = samples

    def check_type_forward(self, in_types):
        type_check.expect(in_types.size() == 3)
        x_type, t_type, w_type = in_types

        type_check.expect(
            x_type.dtype == numpy.float32,
            x_type.ndim == 2,
            t_type.dtype == numpy.int32,
            t_type.ndim == 1,
            x_type.shape[0] == t_type.shape[0],
            w_type.dtype == numpy.float32,
            w_type.ndim == 2,
        )

    def forward_cpu(self, inputs):
        x, t, W = inputs
        self.ignore_mask = (t != self.ignore_label)
        self._make_samples(t)

        loss = numpy.empty(len(x), numpy.float32)
        for i, (it, ix, k) in enumerate(
                six.moves.zip(t, x, self.samples)):
            if it == self.ignore_label:
                loss[i] = 0
            else:
                w = W[k]
                f = w.dot(ix)
                f[0] *= -1  # positive sample
                loss[i] = numpy.sum(numpy.logaddexp(f, 0))

        if self.reduce == 'sum':
            loss = numpy.array(loss.sum(), 'f')
        return loss,

    def forward_gpu(self, inputs):
        x, t, W = inputs
        self.ignore_mask = (t != self.ignore_label)
        n_in = x.shape[1]
        self._make_samples(t)

        self.wx = cuda.elementwise(
            'raw T W, raw T x, bool mask, S k, int32 c, int32 m', 'T wx',
            '''
            T f = 0;
            if (mask == 1) {
                for (int j = 0; j < c; ++j) {
                  int x_ind[] = {(i / m), j};
                  int w_ind[] = {k, j};
                  f += x[x_ind] * W[w_ind];
                }
            }
            wx = f;
            ''',
            'negative_sampling_wx'
        )(W, x, self.ignore_mask[:, None], self.samples, n_in,
          self.sample_size + 1)

        y = cuda.elementwise(
            'T wx, int32 c, int32 m', 'T y',
            '''
            T f = wx;
            if (i % m == 0) {
              f = -f;
            }
            T loss;
            if (f < 0) {
              loss = __logf(1 + __expf(f));
            } else {
              loss = f + __logf(1 + __expf(-f));
            }
            y = loss;
            ''',
            'negative_sampling_forward'
        )(self.wx, n_in, self.sample_size + 1)
        # TODO(okuta): merge elementwise
        loss = y * self.ignore_mask[:, None].astype('float32')
        if self.reduce == 'sum':
            loss = loss.sum()
        else:  # 'none':
            loss = loss.sum(axis=1)
        return loss,

    def backward_cpu(self, inputs, grads):
        x, t, W = inputs
        gloss, = grads

        gx = numpy.zeros_like(x)
        gW = numpy.zeros_like(W)
        for i, active in enumerate(self.ignore_mask):
            if not active:
                continue
            ix = x[i]
            k = self.samples[i]
            if self.reduce == 'sum':
                igy = gloss
            else:
                igy = gloss[i]

            w = W[k]
            f = w.dot(ix)

            # g == -y * gloss / (1 + exp(yf))
            f[0] *= -1
            g = igy / (1 + numpy.exp(-f))
            g[0] *= -1

            gx[i] = g.dot(w)
            for ik, ig in six.moves.zip(k, g):
                gW[ik] += ig * ix
        return gx, None, gW

    def backward_gpu(self, inputs, grads):
        cupy = cuda.cupy
        x, t, W = inputs
        gy, = grads

        n_in = x.shape[1]
        if self.reduce == 'none':
            gy = gy[:, None]
        g = cuda.elementwise(
            'T wx, T gy, int32 m', 'T g',
            '''
            T y;
            if (i % m == 0) {
              y = 1;
            } else {
              y = -1;
            }

            g = -y * gy / (1.0f + __expf(wx * y));
            ''',
            'negative_sampling_calculate_g'
        )(self.wx, gy, self.sample_size + 1)
        gx = cupy.zeros_like(x)
        cuda.elementwise(
            'raw T g, raw T W, bool mask, raw S k, int32 c, int32 m', 'T gx',
            '''
            int d = i / c;
            T w = 0;
            if (mask == 1){
                for (int j = 0; j < m; ++j) {
                  w += g[d * m + j] * W[k[d * m + j] * c + i % c];
                }
            }
            gx = w;
            ''',
            'negative_sampling_calculate_gx'
        )(g, W, self.ignore_mask[:, None], self.samples, n_in,
          self.sample_size + 1, gx)
        gW = cupy.zeros_like(W)
        cuda.elementwise(
            'T g, raw T x, S k, bool mask, int32 c, int32 m',
            'raw T gW',
            '''
            T gi = g;
            if (mask == 1) {
                for (int j = 0; j < c; ++j) {
                  atomicAdd(&gW[k * c + j], gi * x[(i / m) * c + j]);
                }
            }
            ''',
            'negative_sampling_calculate_gw'
        )(g, x, self.samples, self.ignore_mask[:, None], n_in,
          self.sample_size + 1, gW)
        return gx, None, gW


def negative_sampling(x, t, W, sampler, sample_size, reduce='sum'):
    """Negative sampling loss function.

    In natural language processing, especially language modeling, the number of
    words in a vocabulary can be very large.
    Therefore, you need to spend a lot of time calculating the gradient of the
    embedding matrix.

    By using the negative sampling trick you only need to calculate the
    gradient for a few sampled negative examples.

    The objective function is below:

    .. math::

       f(x, p) = \\log \\sigma(x^\\top w_p) +  \\
       k E_{i \\sim P(i)}[\\log \\sigma(- x^\\top w_i)],

    where :math:`\\sigma(\\cdot)` is a sigmoid function, :math:`w_i` is the
    weight vector for the word :math:`i`, and :math:`p` is a positive example.
    It is approximated with :math:`k` examples :math:`N` sampled from
    probability :math:`P(i)`, like this:

    .. math::

       f(x, p) \\approx \\log \\sigma(x^\\top w_p) +  \\
       \\sum_{n \\in N} \\log \\sigma(-x^\\top w_n).

    Each sample of :math:`N` is drawn from the word distribution :math:`P(w)`.
    This is calculated as :math:`P(w) = \\frac{1}{Z} c(w)^\\alpha`, where
    :math:`c(w)` is the unigram count of the word :math:`w`, :math:`\\alpha` is
    a hyper-parameter, and :math:`Z` is the normalization constant.

    Args:
        x (~chainer.Variable): Batch of input vectors.
        t (~chainer.Variable): Vector of ground truth labels.
        W (~chainer.Variable): Weight matrix.
        sampler (~types.FunctionType): Sampling function. It takes a shape and
            returns an integer array of the shape. Each element of this array
            is a sample from the word distribution.
            A :class:`~chainer.utils.WalkerAlias` object built with the power
            distribution of word frequency is recommended.
        sample_size (int): Number of samples.

    See: `Distributed Representations of Words and Phrases and their\
         Compositionality <http://arxiv.org/abs/1310.4546>`_

    .. seealso:: :class:`~chainer.links.NegativeSampling`.

    """
    return NegativeSamplingFunction(sampler, sample_size, reduce)(x, t, W)
