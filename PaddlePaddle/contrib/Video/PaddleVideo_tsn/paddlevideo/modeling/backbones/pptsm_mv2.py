# copyright (c) 2020 PaddlePaddle Authors. All Rights Reserve.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np
import paddle
import paddle_sdaa
from paddle import ParamAttr
import paddle.nn as nn
import paddle.nn.functional as F
from paddle.nn import Conv2D, BatchNorm, Linear, Dropout
from paddle.nn import AdaptiveAvgPool2D, MaxPool2D, AvgPool2D

from ..registry import BACKBONES
from ..weight_init import weight_init_
from ...utils import load_ckpt

# Download URL of pretrained model
# {
# "MobileNetV2":
# "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/MobileNetV2_ssld_pretrained.pdparams",

# "MobileNetV2_x0_25":
# "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/MobileNetV2_x0_25_pretrained.pdparams",
# "MobileNetV2_x0_5":
# "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/MobileNetV2_x0_5_pretrained.pdparams",
# "MobileNetV2_x0_75":
# "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/MobileNetV2_x0_75_pretrained.pdparams",
# "MobileNetV2_x1_5":
# "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/MobileNetV2_x1_5_pretrained.pdparams",
# "MobileNetV2_x2_0":
# "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/MobileNetV2_x2_0_pretrained.pdparams"
# }


class ConvBNLayer(nn.Layer):
    def __init__(self,
                 num_channels,
                 filter_size,
                 num_filters,
                 stride,
                 padding,
                 channels=None,
                 num_groups=1,
                 name=None,
                 use_cudnn=True):
        super(ConvBNLayer, self).__init__()

        self._conv = Conv2D(in_channels=num_channels,
                            out_channels=num_filters,
                            kernel_size=filter_size,
                            stride=stride,
                            padding=padding,
                            groups=num_groups,
                            weight_attr=ParamAttr(name=name + "_weights"),
                            bias_attr=False)

        self._batch_norm = BatchNorm(
            num_filters,
            param_attr=ParamAttr(name=name + "_bn_scale"),
            bias_attr=ParamAttr(name=name + "_bn_offset"),
            moving_mean_name=name + "_bn_mean",
            moving_variance_name=name + "_bn_variance")

    def forward(self, inputs, if_act=True):
        y = self._conv(inputs)
        y = self._batch_norm(y)
        if if_act:
            y = F.relu6(y)
        return y


class InvertedResidualUnit(nn.Layer):
    def __init__(self, num_channels, num_in_filter, num_filters, stride,
                 filter_size, padding, expansion_factor, name, num_seg):
        super(InvertedResidualUnit, self).__init__()
        self.num_seg = num_seg
        num_expfilter = int(round(num_in_filter * expansion_factor))
        self._expand_conv = ConvBNLayer(num_channels=num_channels,
                                        num_filters=num_expfilter,
                                        filter_size=1,
                                        stride=1,
                                        padding=0,
                                        num_groups=1,
                                        name=name + "_expand")

        self._bottleneck_conv = ConvBNLayer(num_channels=num_expfilter,
                                            num_filters=num_expfilter,
                                            filter_size=filter_size,
                                            stride=stride,
                                            padding=padding,
                                            num_groups=num_expfilter,
                                            use_cudnn=False,
                                            name=name + "_dwise")

        self._linear_conv = ConvBNLayer(num_channels=num_expfilter,
                                        num_filters=num_filters,
                                        filter_size=1,
                                        stride=1,
                                        padding=0,
                                        num_groups=1,
                                        name=name + "_linear")

    def forward(self, inputs, ifshortcut):
        # add temporal shift module
        y = inputs
        if ifshortcut:
            y = F.temporal_shift(y, self.num_seg, 1.0 / self.num_seg)

        y = self._expand_conv(y, if_act=True)
        y = self._bottleneck_conv(y, if_act=True)
        y = self._linear_conv(y, if_act=False)
        if ifshortcut:
            y = paddle.add(inputs, y)
        return y


class InvresiBlocks(nn.Layer):
    def __init__(self, in_c, t, c, n, s, name, num_seg):
        super(InvresiBlocks, self).__init__()

        self._first_block = InvertedResidualUnit(num_channels=in_c,
                                                 num_in_filter=in_c,
                                                 num_filters=c,
                                                 stride=s,
                                                 filter_size=3,
                                                 padding=1,
                                                 expansion_factor=t,
                                                 name=name + "_1",
                                                 num_seg=num_seg)

        self._block_list = []
        for i in range(1, n):
            block = self.add_sublayer(name + "_" + str(i + 1),
                                      sublayer=InvertedResidualUnit(
                                          num_channels=c,
                                          num_in_filter=c,
                                          num_filters=c,
                                          stride=1,
                                          filter_size=3,
                                          padding=1,
                                          expansion_factor=t,
                                          name=name + "_" + str(i + 1),
                                          num_seg=num_seg))
            self._block_list.append(block)

    def forward(self, inputs):
        y = self._first_block(inputs, ifshortcut=False)
        for block in self._block_list:
            y = block(y, ifshortcut=True)
        return y


class MobileNet(nn.Layer):
    def __init__(self,
                 class_num=400,
                 scale=1.0,
                 pretrained=None,
                 prefix_name="",
                 num_seg=8):
        super(MobileNet, self).__init__()
        self.scale = scale
        self.class_num = class_num
        self.pretrained = pretrained
        self.num_seg = num_seg

        bottleneck_params_list = [
            (1, 16, 1, 1),
            (6, 24, 2, 2),
            (6, 32, 3, 2),
            (6, 64, 4, 2),
            (6, 96, 3, 1),
            (6, 160, 3, 2),
            (6, 320, 1, 1),
        ]

        self.conv1 = ConvBNLayer(num_channels=3,
                                 num_filters=int(32 * scale),
                                 filter_size=3,
                                 stride=2,
                                 padding=1,
                                 name=prefix_name + "conv1_1")

        self.block_list = []
        i = 1
        in_c = int(32 * scale)
        for layer_setting in bottleneck_params_list:
            t, c, n, s = layer_setting
            i += 1
            block = self.add_sublayer(prefix_name + "conv" + str(i),
                                      sublayer=InvresiBlocks(in_c=in_c,
                                                             t=t,
                                                             c=int(c * scale),
                                                             n=n,
                                                             s=s,
                                                             name=prefix_name +
                                                             "conv" + str(i),
                                                             num_seg=num_seg))
            self.block_list.append(block)
            in_c = int(c * scale)

        self.out_c = int(1280 * scale) if scale > 1.0 else 1280
        self.conv9 = ConvBNLayer(num_channels=in_c,
                                 num_filters=self.out_c,
                                 filter_size=1,
                                 stride=1,
                                 padding=0,
                                 name=prefix_name + "conv9")

        self.pool2d_avg = AdaptiveAvgPool2D(1)

        self.out = Linear(self.out_c,
                          class_num,
                          weight_attr=ParamAttr(name=prefix_name +
                                                "fc10_weights"),
                          bias_attr=ParamAttr(name=prefix_name + "fc10_offset"))

    def init_weights(self):
        """Initiate the parameters.
        """
        if isinstance(self.pretrained, str) and self.pretrained.strip() != "":
            load_ckpt(self, self.pretrained)
        elif self.pretrained is None or self.pretrained.strip() == "":
            for layer in self.sublayers():
                if isinstance(layer, nn.Conv2D):
                    weight_init_(layer, 'KaimingNormal')
                elif isinstance(layer, nn.BatchNorm2D):
                    weight_init_(layer, 'Constant', value=1)

    def forward(self, inputs):
        y = self.conv1(inputs, if_act=True)
        for block in self.block_list:
            y = block(y)
        y = self.conv9(y, if_act=True)
        y = self.pool2d_avg(y)

        y = paddle.reshape(y, [-1, self.num_seg, y.shape[1]])
        y = paddle.mean(y, axis=1)
        y = paddle.reshape(y, shape=[-1, self.out_c])

        y = self.out(y)
        return y


@BACKBONES.register()
def PPTSM_MobileNetV2(pretrained=None, **kwargs):
    model = MobileNet(pretrained=pretrained, scale=1.0, **kwargs)
    return model


def PPTSM_MobileNetV2_x0_25(pretrained=None, **kwargs):
    model = MobileNet(pretrained=pretrained, scale=0.25, **kwargs)
    return model


def PPTSM_MobileNetV2_x0_5(pretrained=None, **kwargs):
    model = MobileNet(pretrained=pretrained, scale=0.5, **kwargs)
    return model


def PPTSM_MobileNetV2_x0_75(pretrained=None, **kwargs):
    model = MobileNet(pretrained=pretrained, scale=0.75, **kwargs)
    return model


def PPTSM_MobileNetV2_x1_5(pretrained=None, **kwargs):
    model = MobileNet(pretrained=pretrained, scale=1.5, **kwargs)
    return model


def PPTSM_MobileNetV2_x2_0(pretrained=None, **kwargs):
    model = MobileNet(pretrained=pretrained, scale=2.0, **kwargs)
    return model
