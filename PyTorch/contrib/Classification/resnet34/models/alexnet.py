import os
import torch
import torch.nn as nn
import torch.utils.model_zoo as model_zoo


# # you need to download the models to ~/.torch/models
# # model_urls = {
# #     'alexnet': 'https://download.pytorch.org/models/alexnet-owt-4df8aa71.pth',
# # }
# models_dir = os.path.expanduser('~/.torch/models')
# model_name = 'alexnet-owt-4df8aa71.pth'

# class AlexNet(nn.Module):
#     def __init__(self, num_classes=1000):
#         super(AlexNet, self).__init__()

#         self.features = nn.Sequential(
#             # input shape is 224 x 224 x 3
#             nn.Conv2d(3, 64, kernel_size=11, stride=4, padding=2), # shape is 55 x 55 x 64
#             nn.ReLU(inplace=True),
#             nn.MaxPool2d(kernel_size=3, stride=2), # shape is 27 x 27 x 64

#             nn.Conv2d(64, 192, kernel_size=5, padding=2), # shape is 27 x 27 x 192
#             nn.ReLU(inplace=True),
#             nn.MaxPool2d(kernel_size=3, stride=2), # shape is 13 x 13 x 192

#             nn.Conv2d(192, 384, kernel_size=3, padding=1), # shape is 13 x 13 x 384
#             nn.ReLU(inplace=True),

#             nn.Conv2d(384, 256, kernel_size=3, padding=1), # shape is 13 x 13 x 256
#             nn.ReLU(inplace=True),

#             nn.Conv2d(256, 256, kernel_size=3, padding=1), # shape is 13 x 13 x 256
#             nn.ReLU(inplace=True),
#             nn.MaxPool2d(kernel_size=3, stride=2) # shape is 6 x 6 x 256
#         )
#         self.classifier = nn.Sequential(
#             nn.Dropout(),
#             nn.Linear(256 * 6 * 6, 4096),
#             nn.ReLU(inplace=True),
#             nn.Dropout(),
#             nn.Linear(4096, 4096),
#             nn.ReLU(inplace=True),
#             nn.Linear(4096, num_classes)
#         )

#     def forward(self, x):
#         x = self.features(x)
#         x = x.reshape(x.size(0), 256 * 6 * 6)
#         x = self.classifier(x)
#         return x


# def alexnet(pretrained=False, **kwargs):
#     """
#     AlexNet model architecture 

#     Args:
#         pretrained (bool): if True, returns a model pre-trained on ImageNet
#     """
#     model = AlexNet(**kwargs)
#     if pretrained:
#         # model.load_state_dict(model_zoo.load_url(model_urls['alexnet']))
#         model.load_state_dict(torch.load(os.path.join(models_dir, model_name)))
#     return model

from functools import partial
from typing import Any, Optional

import torch
import torch.nn as nn
from torchvision.transforms._presets import ImageClassification
from torchvision.utils import _log_api_usage_once
from torchvision.models._api import register_model, Weights, WeightsEnum
from torchvision.models._meta import _IMAGENET_CATEGORIES
from torchvision.models._utils import _ovewrite_named_param, handle_legacy_interface


__all__ = ["AlexNet", "AlexNet_Weights", "alexnet"]


class AlexNet(nn.Module):
    def __init__(self, num_classes: int = 1000, dropout: float = 0.5) -> None:
        super().__init__()
        _log_api_usage_once(self)
        self.features = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=11, stride=4, padding=2),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=3, stride=2),
            nn.Conv2d(64, 192, kernel_size=5, padding=2),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=3, stride=2),
            nn.Conv2d(192, 384, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(384, 256, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=3, stride=2),
        )
        self.avgpool = nn.AdaptiveAvgPool2d((6, 6))
        self.classifier = nn.Sequential(
            nn.Dropout(p=dropout),
            nn.Linear(256 * 6 * 6, 4096),
            nn.ReLU(inplace=True),
            nn.Dropout(p=dropout),
            nn.Linear(4096, 4096),
            nn.ReLU(inplace=True),
            nn.Linear(4096, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        x = self.classifier(x)
        return x


def alexnet(pretrained=False, **kwargs):
    """
    AlexNet model architecture 

    Args:
        pretrained (bool): if True, returns a model pre-trained on ImageNet
    """
    model = AlexNet(**kwargs)
    if pretrained:
        # model.load_state_dict(model_zoo.load_url(model_urls['alexnet']))
        model.load_state_dict(torch.load(os.path.join(models_dir, model_name)))
    return model