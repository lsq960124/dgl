"""Torch Module for Chebyshev Spectral Graph Convolution layer"""
# pylint: disable= no-member, arguments-differ, invalid-name
import torch as th
from torch import nn
import torch.nn.functional as F

from ....base import dgl_warning
from .... import laplacian_lambda_max, broadcast_nodes, function as fn


class ChebConv(nn.Module):
    r"""Chebyshev Spectral Graph Convolution layer from paper `Convolutional
    Neural Networks on Graphs with Fast Localized Spectral Filtering
    <https://arxiv.org/pdf/1606.09375.pdf>`__.

    .. math::
        h_i^{l+1} &= \sum_{k=0}^{K-1} W^{k, l}z_i^{k, l}

        Z^{0, l} &= H^{l}

        Z^{1, l} &= \hat{L} \cdot H^{l}

        Z^{k, l} &= 2 \cdot \hat{L} \cdot Z^{k-1, l} - Z^{k-2, l}

        \hat{L} &= 2\left(I - \hat{D}^{-1/2} \hat{A} \hat{D}^{-1/2}\right)/\lambda_{max} - I

    Parameters
    ----------
    in_feats: int
        Number of input features.
    out_feats: int
        Number of output features.
    k : int
        Chebyshev filter size.
    activation : function, optional
        Activation function, default is ReLu.
    bias : bool, optional
        If True, adds a learnable bias to the output. Default: ``True``.
    """

    def __init__(self,
                 in_feats,
                 out_feats,
                 k,
                 activation=F.relu,
                 bias=True):
        super(ChebConv, self).__init__()
        self._k = k
        self._in_feats = in_feats
        self._out_feats = out_feats
        self.activation = activation
        self.linear = nn.Linear(k * in_feats, out_feats, bias)

    def forward(self, graph, feat, lambda_max=None):
        r"""Compute ChebNet layer.

        Parameters
        ----------
        graph : DGLGraph
            The graph.
        feat : torch.Tensor
            The input feature of shape :math:`(N, D_{in})` where :math:`D_{in}`
            is size of input feature, :math:`N` is the number of nodes.
        lambda_max : list or tensor or None, optional.
            A list(tensor) with length :math:`B`, stores the largest eigenvalue
            of the normalized laplacian of each individual graph in ``graph``,
            where :math:`B` is the batch size of the input graph. Default: None.
            If None, this method would compute the list by calling
            ``dgl.laplacian_lambda_max``.

        Returns
        -------
        torch.Tensor
            The output feature of shape :math:`(N, D_{out})` where :math:`D_{out}`
            is size of output feature.
        """
        def unnLaplacian(feat, D_invsqrt, graph):
            """ Operation Feat * D^-1/2 A D^-1/2 """
            graph.ndata['h'] = feat * D_invsqrt
            graph.update_all(fn.copy_u('h', 'm'), fn.sum('m', 'h'))
            return graph.ndata.pop('h') * D_invsqrt

        with graph.local_scope():
            D_invsqrt = th.pow(graph.in_degrees().float().clamp(
                min=1), -0.5).unsqueeze(-1).to(feat.device)

            if lambda_max is None:
                try:
                    lambda_max = laplacian_lambda_max(graph)
                except BaseException:
                    # if the largest eigenvalue is not found
                    dgl_warning(
                        "Largest eigonvalue not found, using default value 2 for lambda_max",
                        RuntimeWarning)
                    lambda_max = th.Tensor(2).to(feat.device)

            if isinstance(lambda_max, list):
                lambda_max = th.Tensor(lambda_max).to(feat.device)
            if lambda_max.dim() == 1:
                lambda_max = lambda_max.unsqueeze(-1)  # (B,) to (B, 1)

            # broadcast from (B, 1) to (N, 1)
            lambda_max = broadcast_nodes(graph, lambda_max)
            re_norm = 2. / lambda_max

            # X_0 is the raw feature, Xt refers to the concatenation of X_0, X_1, ... X_t
            Xt = X_0 = feat

            # X_1(f)
            if self._k > 1:
                h = unnLaplacian(X_0, D_invsqrt, graph)
                X_1 = - re_norm * h + X_0 * (re_norm - 1)
                # Concatenate Xt and X_1
                Xt = th.cat((Xt, X_1), 1)

            # Xi(x), i = 2...k
            for _ in range(2, self._k):
                h = unnLaplacian(X_1, D_invsqrt, graph)
                X_i = - 2 * re_norm * h + X_1 * 2 * (re_norm - 1) - X_0
                # Concatenate Xt and X_i
                Xt = th.cat((Xt, X_i), 1)
                X_1, X_0 = X_i, X_1

            # linear projection
            h = self.linear(Xt)

            # activation
            if self.activation:
                h = self.activation(h)

        return h
