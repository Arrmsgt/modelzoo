"""
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
"""

import copy
import paddle
import paddle_sdaa


def build_optimizer(cfg, lr_scheduler, parameter_list=None):

    """
    Build an optimizer and learning rate scheduler to optimize parameters accroding to ```OPTIMIZER``` field in configuration .

    In configuration:
    OPTIMIZER:
        name: Momentum
        momentum: 0.9
        weight_decay: 0.001
    or

    OPTIMIZER:
        name: Momentum
        momentum: 0.9
        weight_decay:
            name: "L1"
            value: 0.001

    Momentum optimizer will be applied to optimize network and L1Decay regularizer will be applied to avoid overfit.

    OPTIMIZER:
        name: Adam
        weight_decay:
            name: "L2"
            value: 0.001

    Adam optimizer will be applied to optimize network and L2Decay regularizer will applied to avoid overfit.

    Refer to ```https://www.paddlepaddle.org.cn/documentation/docs/en/develop/api/paddle/regularizer/L2Decay_en.html``` for more details.

    Args:
        cfg (dict): optimizer configuration.
        lr_schduler: learning rate scheduler.
        parameter_list (list): parameters to be optimized.

    Returns:
        optimizer (paddle.optimizer): paddle optimizer.

    """
    

    cfg_copy = cfg.copy()
    #XXX check none and illegal cfg!!!
    opt_name = cfg_copy.pop('name')
    # deal with weight decay
    if cfg_copy.get('weight_decay'):
        if isinstance(cfg_copy.get('weight_decay'), float) or 'L1' in cfg_copy.get('weight_decay').get('name').upper():
            cfg_copy['weight_decay'] = cfg_copy.get('weight_decay').get('value')
        elif 'L2' in cfg_copy.get('weight_decay').get('name').upper():
            cfg_copy['weight_decay'] = paddle.regularizer.L2Decay(cfg_copy.get('weight_decay').get('value'))
        else:
            raise ValueError

    cfg_copy.pop('learning_rate')

    return getattr(paddle.optimizer, opt_name)(lr_scheduler,
                                               parameters=parameter_list,
                                               **cfg_copy)
