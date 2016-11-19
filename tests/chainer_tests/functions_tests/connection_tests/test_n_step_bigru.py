import unittest

import mock
import numpy

import chainer
from chainer import cuda
from chainer import functions
from chainer import gradient_check
from chainer import testing
from chainer.testing import attr


def sigmoid(x):
    return numpy.tanh(x * 0.5) * 0.5 + 0.5


def _split(inputs, pos):
    return inputs[:pos], inputs[pos:]


@testing.parameterize(*testing.product({
    'use_cudnn': [True, False],
}))
class TestNStepBiGRU(unittest.TestCase):

    batches = [5, 4, 3, 2]
    length = len(batches)  # max sequence length
    in_size = 2
    out_size = 3
    n_layers = 3
    dropout = 0.0

    def setUp(self):
        self.xs = [numpy.random.uniform(-1, 1, (b, self.in_size)).astype('f')
                   for b in self.batches]
        h_shape = (self.n_layers * 2, self.batches[0], self.out_size)
        self.hx = numpy.random.uniform(-1, 1, h_shape).astype(numpy.float32)

        self.ws = []
        self.bs = []
        for i in range(self.n_layers):
            for di in [0, 1]:
                weights = []
                biases = []
                for j in range(6):
                    if i == 0 and j < 3:
                        w_in = self.in_size
                    elif i > 0 and j < 3:
                        w_in = self.out_size * 2
                    else:
                        w_in = self.out_size
                    weights.append(numpy.random.uniform(-1, 1, (self.out_size, w_in)).astype('f'))
                    biases.append(numpy.random.uniform(-1, 1, (self.out_size,)).astype('f'))
                self.ws.append(weights)
                self.bs.append(biases)

        self.dys = [numpy.random.uniform(-1, 1, (b, self.out_size * 2)).astype('f')
                    for b in self.batches]
        self.dcy = numpy.random.uniform(-1, 1, h_shape).astype(numpy.float32)
        self.dhy = numpy.random.uniform(-1, 1, h_shape).astype(numpy.float32)

    def check_forward(
            self, h_data, xs_data, ws_data, bs_data, volatile):
        h = chainer.Variable(h_data, volatile=volatile)
        xs = [chainer.Variable(x, volatile=volatile) for x in xs_data]
        ws = [[chainer.Variable(w, volatile=volatile) for w in ws]
              for ws in ws_data]
        bs = [[chainer.Variable(b, volatile=volatile) for b in bs]
              for bs in bs_data]

        hy, ys = functions.n_step_bigru(
            self.n_layers, self.dropout, h, ws, bs, xs,
            use_cudnn=self.use_cudnn)

        e_hy = self.hx.copy()
        xs = self.xs
        for layer in range(self.n_layers):
            # forward
            di = 0
            xf = []
            for ind in range(self.length):
                x = xs[ind]
                batch = x.shape[0]  # current batch size
                w = self.ws[2 * layer + di]
                b = self.bs[2 * layer + di]
                h_prev = e_hy[2 * layer + di, :batch]
                r = sigmoid(x.dot(w[0].T) + h_prev.dot(w[3].T) + b[0] + b[3])
                z = sigmoid(x.dot(w[1].T) + h_prev.dot(w[4].T) + b[1] + b[4])
                h_bar = z * h_prev + (1 - z) * numpy.tanh(x.dot(w[2].T) + b[2] + r * (h_prev.dot(w[5].T) + b[5]))
                e_hy[2 * layer + di, :batch] = h_bar
                xf.append(h_bar)

            # backward
            di = 1
            xb = []
            for ind in reversed(range(self.length)):
                x = xs[ind]
                batch = x.shape[0]
                w = self.ws[2 * layer + di]
                b = self.bs[2 * layer + di]
                h_prev = e_hy[2 * layer + di, :batch]
                r = sigmoid(x.dot(w[0].T) + h_prev.dot(w[3].T) + b[0] + b[3])
                z = sigmoid(x.dot(w[1].T) + h_prev.dot(w[4].T) + b[1] + b[4])
                h_bar = z * h_prev + (1 - z) * numpy.tanh(x.dot(w[2].T) + b[2] + r * (h_prev.dot(w[5].T) + b[5]))
                e_hy[2 * layer + di, :batch] = h_bar
                xb.append(h_bar)

            # new layer inputs
            xb.reverse()
            xs = [numpy.concatenate([hf, hb], axis=1) for (hf, hb) in zip(xf, xb)]

        for k, (ysi, xsi) in enumerate(zip(ys, xs)):
            testing.assert_allclose(ysi.data, xsi, rtol=1e-4, atol=1e-4)

        testing.assert_allclose(hy.data, e_hy, rtol=1e-4, atol=1e-4)

    def test_forward_cpu(self):
        self.check_forward(self.hx, self.xs, self.ws, self.bs, False)

    def test_forward_cpu_volatile(self):
        self.check_forward(self.hx, self.xs, self.ws, self.bs, True)

    @attr.gpu
    def test_forward_gpu(self):
        self.check_forward(cuda.to_gpu(self.hx),
                           [cuda.to_gpu(x) for x in self.xs],
                           [[cuda.to_gpu(w) for w in ws] for ws in self.ws],
                           [[cuda.to_gpu(b) for b in bs] for bs in self.bs],
                           False)

    @attr.gpu
    def test_forward_gpu_volatile(self):
        self.check_forward(cuda.to_gpu(self.hx),
                           [cuda.to_gpu(x) for x in self.xs],
                           [[cuda.to_gpu(w) for w in ws] for ws in self.ws],
                           [[cuda.to_gpu(b) for b in bs] for bs in self.bs],
                           True)

    def check_backward(self, h_data, xs_data, ws_data, bs_data,
                       dhy_data, dys_data):
        args = tuple([h_data, ] + sum(ws_data, []) + sum(bs_data, []) +
                     xs_data)
        grads = tuple([dhy_data, ] + dys_data)

        def f(*inputs):
            (hx,), inputs = _split(inputs, 1)
            ws = []
            for i in range(self.n_layers * 2):
                weights, inputs = _split(inputs, 6)
                ws.append(weights)
            bs = []
            for i in range(self.n_layers * 2):
                biases, inputs = _split(inputs, 6)
                bs.append(biases)
            xs = inputs
            hy, ys = functions.n_step_bigru(
                self.n_layers, self.dropout, hx, ws, bs, xs)
            return (hy, ) + ys

        gradient_check.check_backward(
            f, args, grads, eps=1e-2, rtol=1e-3, atol=1e-3)

    def test_backward_cpu(self):
        self.check_backward(self.hx, self.xs, self.ws, self.bs,
                            self.dhy, self.dys)

    @attr.gpu
    def test_backward_gpu(self):
        self.check_backward(cuda.to_gpu(self.hx),
                            [cuda.to_gpu(x) for x in self.xs],
                            [[cuda.to_gpu(w) for w in ws] for ws in self.ws],
                            [[cuda.to_gpu(b) for b in bs] for bs in self.bs],
                            cuda.to_gpu(self.dhy),
                            [cuda.to_gpu(dy) for dy in self.dys])


@testing.parameterize(*testing.product({
    'use_cudnn': [True, False],
}))
@attr.cudnn
class TestNStepGRUCudnnCall(unittest.TestCase):
    batches = [7, 6, 5]
    length = len(batches)
    in_size = 3
    out_size = 4
    n_layers = 2
    dropout = 0.0

    def setUp(self):
        self.xs = [cuda.cupy.random.uniform(-1, 1, (b, self.in_size)).astype('f') for b in self.batches]
        h_shape = (self.n_layers * 2, self.batches[0], self.out_size)
        self.hx = cuda.cupy.random.uniform(-1, 1, h_shape).astype('f')

        self.ws = []
        self.bs = []
        for i in range(self.n_layers):
            for di in [0, 1]:
                weights = []
                biases = []
                for j in range(6):
                    if i == 0 and j < 3:
                        w_in = self.in_size
                    elif i > 0 and j < 3:
                        w_in = self.out_size * 2
                    else:
                        w_in = self.out_size

                    weights.append(cuda.cupy.random.uniform(-1, 1, (self.out_size, w_in)).astype('f'))
                    biases.append(cuda.cupy.random.uniform(-1, 1, (self.out_size,)).astype('f'))

                self.ws.append(weights)
                self.bs.append(biases)

        self.dhy = cuda.cupy.random.uniform(-1, 1, h_shape).astype('f')
        self.expect = self.use_cudnn and (cuda.cudnn.cudnn.getVersion() >= 5000)

    def forward(self, train):
        volatile = not train
        h = chainer.Variable(self.hx, volatile=volatile)
        xs = [chainer.Variable(x, volatile=volatile) for x in self.xs]
        ws = [[chainer.Variable(w, volatile=volatile) for w in ws] for ws in self.ws]
        bs = [[chainer.Variable(b, volatile=volatile) for b in bs] for bs in self.bs]

        return functions.n_step_bigru(
            self.n_layers, self.dropout, h, ws, bs, xs,
            train=train, use_cudnn=self.use_cudnn)

    def test_call_cudnn_forward_training(self):
        with mock.patch('cupy.cuda.cudnn.RNNForwardTraining') as func:
            self.forward(True)
            self.assertEqual(func.called, self.expect)

    def test_call_cudnn_forward_inference(self):
        with mock.patch('cupy.cuda.cudnn.RNNForwardInference') as func:
            self.forward(False)
            self.assertEqual(func.called, self.expect)

    def test_call_cudnn_backward(self):
        hy, ys = self.forward(True)
        hy.grad = self.dhy
        with mock.patch('cupy.cuda.cudnn.RNNBackwardWeights') as func:
            hy.backward()
            self.assertEqual(func.called, self.expect)

testing.run_module(__name__, __file__)
