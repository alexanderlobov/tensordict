# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from numbers import Number
from typing import Dict, Sequence, Tuple, Union

import numpy as np
import torch

from tensordict.nn.utils import mappings
from torch import distributions as D, nn

__all__ = ["NormalParamWrapper", "Delta"]

# speeds up distribution construction
D.Distribution.set_default_validate_args(False)


class NormalParamWrapper(nn.Module):
    """A wrapper for normal distribution parameters.

    Args:
        operator (nn.Module): operator whose output will be transformed_in in location and scale parameters
        scale_mapping (str, optional): positive mapping function to be used with the std.
            default = "biased_softplus_1.0" (i.e. softplus map with bias such that fn(0.0) = 1.0)
            choices: "softplus", "exp", "relu", "biased_softplus_1";
        scale_lb (Number, optional): The minimum value that the variance can take. Default is 1e-4.

    Examples:
        >>> from torch import nn
        >>> import torch
        >>> module = nn.Linear(3, 4)
        >>> module_normal = NormalParamWrapper(module)
        >>> tensor = torch.randn(3)
        >>> loc, scale = module_normal(tensor)
        >>> print(loc.shape, scale.shape)
        torch.Size([2]) torch.Size([2])
        >>> assert (scale > 0).all()
        >>> # with modules that return more than one tensor
        >>> module = nn.LSTM(3, 4)
        >>> module_normal = NormalParamWrapper(module)
        >>> tensor = torch.randn(4, 2, 3)
        >>> loc, scale, others = module_normal(tensor)
        >>> print(loc.shape, scale.shape)
        torch.Size([4, 2, 2]) torch.Size([4, 2, 2])
        >>> assert (scale > 0).all()

    """

    def __init__(
        self,
        operator: nn.Module,
        scale_mapping: str = "biased_softplus_1.0",
        scale_lb: Number = 1e-4,
    ) -> None:
        super().__init__()
        self.operator = operator
        self.scale_mapping = scale_mapping
        self.scale_lb = scale_lb

    def forward(self, *tensors: torch.Tensor) -> Tuple[torch.Tensor]:
        net_output = self.operator(*tensors)
        others = ()
        if not isinstance(net_output, torch.Tensor):
            net_output, *others = net_output
        loc, scale = net_output.chunk(2, -1)
        scale = mappings(self.scale_mapping)(scale).clamp_min(self.scale_lb)
        return (loc, scale, *others)


class Delta(D.Distribution):
    """Delta distribution.

    Args:
        param (torch.Tensor): parameter of the delta distribution;
        atol (number, optional): absolute tolerance to consider that a tensor matches the distribution parameter;
            Default is 1e-6
        rtol (number, optional): relative tolerance to consider that a tensor matches the distribution parameter;
            Default is 1e-6
        batch_shape (torch.Size, optional): batch shape;
        event_shape (torch.Size, optional): shape of the outcome.

    """

    arg_constraints: Dict = {}

    def __init__(
        self,
        param: torch.Tensor,
        atol: float = 1e-6,
        rtol: float = 1e-6,
        batch_shape: Union[torch.Size, Sequence[int]] = None,
        event_shape: Union[torch.Size, Sequence[int]] = None,
    ):
        if batch_shape is None:
            batch_shape = torch.Size([])
        if event_shape is None:
            event_shape = torch.Size([])
        self.update(param)
        self.atol = atol
        self.rtol = rtol
        if not len(batch_shape) and not len(event_shape):
            batch_shape = param.shape[:-1]
            event_shape = param.shape[-1:]
        super().__init__(batch_shape=batch_shape, event_shape=event_shape)

    def update(self, param):
        self.param = param

    def _is_equal(self, value: torch.Tensor) -> torch.Tensor:
        param = self.param.expand_as(value)
        is_equal = abs(value - param) < self.atol + self.rtol * abs(param)
        for i in range(-1, -len(self.event_shape) - 1, -1):
            is_equal = is_equal.all(i)
        return is_equal

    def log_prob(self, value: torch.Tensor) -> torch.Tensor:
        is_equal = self._is_equal(value)
        out = torch.zeros_like(is_equal, dtype=value.dtype)
        out.masked_fill_(is_equal, np.inf)
        out.masked_fill_(~is_equal, -np.inf)
        return out

    @torch.no_grad()
    def sample(self, size=None) -> torch.Tensor:
        if size is None:
            size = torch.Size([])
        return self.param.expand(*size, *self.param.shape)

    def rsample(self, size=None) -> torch.Tensor:
        if size is None:
            size = torch.Size([])
        return self.param.expand(*size, *self.param.shape)

    @property
    def mode(self) -> torch.Tensor:
        return self.param

    @property
    def mean(self) -> torch.Tensor:
        return self.param
