# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Modified: retain only H3-required kernels; use int64 matrix pointer offsets.
import triton
import triton.language as tl

@triton.autotune(
    configs=[
        triton.Config({'block_m': 128, 'block_n': 256, 'block_k': 64, 'group_size_m': 8}, num_stages=3, num_warps=8),
        triton.Config({'block_m': 64,  'block_n': 256, 'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
        triton.Config({'block_m': 128, 'block_n': 128, 'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
        triton.Config({'block_m': 128, 'block_n': 64,  'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
        triton.Config({'block_m': 64,  'block_n': 128, 'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
        triton.Config({'block_m': 128, 'block_n': 32,  'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
    ],
    key=['m', 'n', 'k'],
)
@triton.jit
def _int8_matmul_dequant_kernel(
    # Pointers
    a_ptr, b_ptr, c_ptr,
    a_scale_ptr, b_scale_ptr, bias_ptr,
    # Matrix Dimensions
    m, n, k,
    # Strides
    stride_am, stride_ak,
    stride_bk, stride_bn,
    stride_cm, stride_cn,
    # Meta-parameters
    block_m: tl.constexpr, block_n: tl.constexpr, block_k: tl.constexpr,
    group_size_m: tl.constexpr,
    has_bias: tl.constexpr
):
    """
    Computes: C = ((A * B) * (scale_a * scale_b)) + bias
    A: [m, k] int8
    B: [n, k] int8 (Transposed physically or logically via strides)
    """
    pid = tl.program_id(axis=0)
    num_pid_m = tl.cdiv(m, block_m)
    num_pid_n = tl.cdiv(n, block_n)
    num_pid_in_group = group_size_m * num_pid_n
    group_id = pid // num_pid_in_group
    first_pid_m = group_id * group_size_m
    actual_group_size_m = min(num_pid_m - first_pid_m, group_size_m)
    pid_m = first_pid_m + (pid % actual_group_size_m)
    pid_n = (pid % num_pid_in_group) // actual_group_size_m

    # 1. Prepare Pointers for A and B
    offs_am = ((pid_m * block_m + tl.arange(0, block_m)) % m).to(tl.int64)
    offs_bn = ((pid_n * block_n + tl.arange(0, block_n)) % n).to(tl.int64)
    offs_k = tl.arange(0, block_k)

    a_ptrs = a_ptr + (offs_am[:, None] * stride_am + offs_k[None, :] * stride_ak)
    b_ptrs = b_ptr + (offs_k[:, None] * stride_bk + offs_bn[None, :] * stride_bn)

    # 2. Main Loop (Accumulate in Int32)
    accumulator = tl.zeros((block_m, block_n), dtype=tl.int32)

    for k_idx in range(0, tl.cdiv(k, block_k)):
        # Load chunks
        a = tl.load(a_ptrs, mask=offs_k[None, :] < k - k_idx * block_k, other=0)
        b = tl.load(b_ptrs, mask=offs_k[:, None] < k - k_idx * block_k, other=0)

        # Matrix Multiply
        accumulator += tl.dot(a, b)

        # Advance pointers
        a_ptrs += block_k * stride_ak
        b_ptrs += block_k * stride_bk

    # 3. Fused Epilogue (Dequantize & Bias)
    scale_a = tl.load(a_scale_ptr + offs_am) # Vector [BLOCK_M]
    scale_b = tl.load(b_scale_ptr)

    c = accumulator.to(tl.float32)
    total_scale = scale_a[:, None] * scale_b
    c = c * total_scale

    if has_bias:
        bias = tl.load(bias_ptr + offs_bn) # Vector [BLOCK_N]
        c = c + bias[None, :]

    # 4. Store Result
    c_ptrs = c_ptr + stride_cm * offs_am[:, None] + stride_cn * offs_bn[None, :]
    c_mask = (offs_am[:, None] < m) & (offs_bn[None, :] < n)
    tl.store(c_ptrs, c, mask=c_mask)


@triton.autotune(
    configs=[
        triton.Config({'block_m': 128, 'block_n': 256, 'block_k': 64, 'group_size_m': 8}, num_stages=3, num_warps=8),
        triton.Config({'block_m': 64,  'block_n': 256, 'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
        triton.Config({'block_m': 128, 'block_n': 128, 'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
        triton.Config({'block_m': 128, 'block_n': 64,  'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
        triton.Config({'block_m': 64,  'block_n': 128, 'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
        triton.Config({'block_m': 128, 'block_n': 32,  'block_k': 32, 'group_size_m': 8}, num_stages=4, num_warps=4),
    ],
    key=['m', 'n', 'k'],
)
@triton.jit
def _int8_matmul_dequant_per_row_kernel(
    # Pointers
    a_ptr, b_ptr, c_ptr,
    a_scale_ptr, b_scale_ptr, bias_ptr,
    # Matrix Dimensions
    m, n, k,
    # Strides
    stride_am, stride_ak,
    stride_bk, stride_bn,
    stride_cm, stride_cn,
    # Meta-parameters
    block_m: tl.constexpr, block_n: tl.constexpr, block_k: tl.constexpr,
    group_size_m: tl.constexpr,
    has_bias: tl.constexpr
):
    """
    Computes: C = ((A * B) * (scale_a[:, None] * scale_b[None, :])) + bias
    A: [m, k] int8, scale_a: [m, 1] per-row activation scales
    B: [n, k] int8, scale_b: [n, 1] per-row weight scales
    """
    pid = tl.program_id(axis=0)
    num_pid_m = tl.cdiv(m, block_m)
    num_pid_n = tl.cdiv(n, block_n)
    num_pid_in_group = group_size_m * num_pid_n
    group_id = pid // num_pid_in_group
    first_pid_m = group_id * group_size_m
    actual_group_size_m = min(num_pid_m - first_pid_m, group_size_m)
    pid_m = first_pid_m + (pid % actual_group_size_m)
    pid_n = (pid % num_pid_in_group) // actual_group_size_m

    # 1. Prepare Pointers for A and B
    offs_am = ((pid_m * block_m + tl.arange(0, block_m)) % m).to(tl.int64)
    offs_bn = ((pid_n * block_n + tl.arange(0, block_n)) % n).to(tl.int64)
    offs_k = tl.arange(0, block_k)

    a_ptrs = a_ptr + (offs_am[:, None] * stride_am + offs_k[None, :] * stride_ak)
    b_ptrs = b_ptr + (offs_k[:, None] * stride_bk + offs_bn[None, :] * stride_bn)

    # 2. Main Loop (Accumulate in Int32)
    accumulator = tl.zeros((block_m, block_n), dtype=tl.int32)

    for k_idx in range(0, tl.cdiv(k, block_k)):
        a = tl.load(a_ptrs, mask=offs_k[None, :] < k - k_idx * block_k, other=0)
        b = tl.load(b_ptrs, mask=offs_k[:, None] < k - k_idx * block_k, other=0)
        accumulator += tl.dot(a, b)
        a_ptrs += block_k * stride_ak
        b_ptrs += block_k * stride_bk

    # 3. Fused Epilogue (Dequantize & Bias)
    scale_a = tl.load(a_scale_ptr + offs_am)  # Vector [BLOCK_M]
    scale_b = tl.load(b_scale_ptr + offs_bn)  # Vector [BLOCK_N]

    c = accumulator.to(tl.float32)
    total_scale = scale_a[:, None] * scale_b[None, :]
    c = c * total_scale

    if has_bias:
        bias = tl.load(bias_ptr + offs_bn)
        c = c + bias[None, :]

    # 4. Store Result
    c_ptrs = c_ptr + stride_cm * offs_am[:, None] + stride_cn * offs_bn[None, :]
    c_mask = (offs_am[:, None] < m) & (offs_bn[None, :] < n)
    tl.store(c_ptrs, c, mask=c_mask)

