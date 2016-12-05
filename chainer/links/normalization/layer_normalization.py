from chainer import functions
from chainer import initializers
from chainer import link
from chainer import links


class LayerNormalization(link.Chain):

    """Layer normalization layer on outputs of linear functions.

    This is a link of "Layer Normalization". This layer
    normalizes, scales and shifts input units with :link:`~chainer.links.Scale`.

    Args:
        size (int): Size of input units.
        eps (float): Epsilon value for numerical stability of the normalization.
        initial_gumma (~chainer.Initializer): Initializer for the scale vector.
            If ``None``, then the vector is initialized
            by :class:`~chainer.initializers.HeNormal`.
            If a scalar, the vectors are filled by it.
            If ``numpy.ndarray``, the vectors are set by it.
        initial_beta (~chainer.Initializer): Initializer for the shift vector.
            If ``None``, then the vector is initialized
            by :class:`~chainer.initializers.HeNormal`.
            If a scalar, the vectors are filled by it.
            If ``numpy.ndarray``, the vectors are set by it.

    See: `Layer Normalization <https://arxiv.org/abs/1607.06450>`_
    """

    def __init__(self, size, eps=1e-6, initial_gamma=None, initial_beta=None):
        super(LayerNormalization, self).__init__(
            scale=links.Scale(axis=1, W_shape=(size, ), bias_term=True),
        )
        if initial_gamma is None:
            initial_gamma = initializers.One()
        initializers.init_weight(self.scale.W.data, initial_gamma)
        if initial_beta is None:
            initial_beta = initializers.Zero()
        initializers.init_weight(self.scale.bias.b.data, initial_beta)
        self.eps = eps

    def _normalize(self, x):
        size = x.shape[1]
        mean = functions.broadcast_to(
            (functions.sum(x, axis=1) / size)[:, None],
            x.shape)
        std = functions.broadcast_to(functions.sqrt(
            functions.sum(functions.square(x - mean), axis=1) / size)[:, None],
            x.shape) + self.eps
        return (x - mean) / std

    def __call__(self, x):
        return self.scale(self._normalize(x))
