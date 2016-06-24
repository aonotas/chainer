import unittest

import functools
import itertools
import numpy
from operator import mul
import six

import chainer
from chainer import cuda
from chainer import functions
from chainer import gradient_check
from chainer import testing
from chainer.testing import attr
from chainer.testing import condition
from chainer.utils import conv


@testing.parameterize(*testing.product({
    'cover_all': [True, False],
    'dtype': [numpy.float32],  # numpy.float32, numpy.float64],
}))
class TestMaxPoolingND(unittest.TestCase):

    def setUp(self):
#        self.ds = (4, 3, 2)
        self.ds = (4, 3)
        self.N = len(self.ds)
        self.ks = (3,) * self.N
        self.stride = (2,) * self.N
        self.pad = (1,) * self.N

        # Avoid unstability of numerical gradient
        x_shape = (2, 3) + self.ds
        self.x = numpy.arange(
            functools.reduce(mul, x_shape), dtype=self.dtype).reshape(x_shape)
        self.x = 2 * self.x / self.x.size - 1

        outs = tuple([conv.get_conv_outsize(d, k, s, p, self.cover_all)
                      for (d, k, s, p)
                      in zip(self.ds, self.ks, self.stride, self.pad)])
        gy_shape = (2, 3) + outs
        self.gy = numpy.random.uniform(-1, 1, gy_shape).astype(self.dtype)

        self.check_backward_options = {'eps': 2.0 ** -8}

    def check_forward(self, x_data, use_cudnn=True):
        def _patches(ds, ks, ss, ps, cover_all):
            """Return tuples of slices that indicate pooling patches."""
            # Left-top indeces of each pooling patch.
            if cover_all:
                xss = itertools.product(
                    *[six.moves.range(-p, d+p-k+s, s)
                      for (d, k, s, p) in zip(ds, ks, ss, ps)])
            else:
                xss = itertools.product(
                    *[six.moves.range(-p, d+p-k+1, s)
                      for (d, k, s, p) in zip(ds, ks, ss, ps)])
            # Tuple of slices for pooling patches.
            return [tuple([slice(max(x, 0), min(x+k, d))
                           for (x, d, k) in zip(xs, ds, ks)])
                    for xs in xss]

        N = self.N
        ds = self.ds
        ks = self.ks
        ss = self.stride
        ps = self.pad
        x = chainer.Variable(x_data)
        y = functions.max_pooling_nd(x, N, ks, ss, ps,
                                     cover_all=self.cover_all,
                                     use_cudnn=use_cudnn)
        self.assertEqual(y.data.dtype, self.dtype)
        y_data = cuda.to_cpu(y.data)

        self.assertEqual(self.gy.shape, y_data.shape)
        for k in six.moves.range(2):
            for c in six.moves.range(3):
                x = self.x[k, c]
                expect = numpy.array(
                    [x[idx].max()
                     for idx in _patches(ds, ks, ss, ps, self.cover_all)]
                ).reshape(y_data.shape[2:])
                gradient_check.assert_allclose(expect, y_data[k, c])

    @condition.retry(3)
    def test_forward_cpu(self):
        self.check_forward(self.x, use_cudnn=False)

    # TODO(takagi) test_forward_cpu_wide?

    @attr.cudnn
    @condition.retry(3)
    def test_forward_gpu(self):
        self.check_forward(cuda.to_gpu(self.x))

    @attr.gpu
    @condition.retry(3)
    def test_forward_gpu_no_cudnn(self):
        self.check_forward(cuda.to_gpu(self.x), False)

    def check_backward(self, x_data, y_grad, use_cudnn=True):
        gradient_check.check_backward(
            functions.MaxPoolingND(self.N, self.ks, self.stride, self.pad,
                                   cover_all=self.cover_all,
                                   use_cudnn=use_cudnn),
            x_data, y_grad, **self.check_backward_options)

    @condition.retry(3)
    def test_backward_cpu(self):
        self.check_backward(self.x, self.gy)

    @attr.cudnn
    @condition.retry(3)
    def test_backward_gpu(self):
        self.check_backward(cuda.to_gpu(self.x), cuda.to_gpu(self.gy))

    @attr.gpu
    @condition.retry(3)
    def test_backward_gpu_no_cudnn(self):
        self.check_backward(cuda.to_gpu(self.x), cuda.to_gpu(self.gy), False)


testing.run_module(__name__, __file__)
