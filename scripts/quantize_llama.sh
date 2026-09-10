#!/usr/bin/env bash
# 模型量化脚本：FP16 → INT8(q8_0) / INT4(q4_K_M)（llama.cpp GGUF 路线）
# 前置：已 clone llama.cpp 并编译 convert/quantize 工具；已下载原始模型权重。
#
# 用法：
#   bash scripts/quantize_llama.sh <原始模型目录> <输出目录>
# 示例：
#   bash scripts/quantize_llama.sh ~/models/Qwen2.5-7B-Instruct ~/models/quantized
set -euo pipefail

SRC="${1:?用法: quantize_llama.sh <原始模型目录> <输出目录>}"
OUT="${2:?用法: quantize_llama.sh <原始模型目录> <输出目录>}"
LLAMA_CPP="${LLAMA_CPP_DIR:-$HOME/llama.cpp}"

mkdir -p "$OUT"

echo "== 1/3 转换为 GGUF（F16）=="
python "$LLAMA_CPP/convert_hf_to_gguf.py" "$SRC" --outfile "$OUT/model-f16.gguf" --outtype f16

echo "== 2/3 量化 INT8 (q8_0) =="
"$LLAMA_CPP/build/llama-quantize" "$OUT/model-f16.gguf" "$OUT/model-q8_0.gguf" q8_0

echo "== 3/3 量化 INT4 (q4_K_M) =="
"$LLAMA_CPP/build/llama-quantize" "$OUT/model-f16.gguf" "$OUT/model-q4_K_M.gguf" q4_K_M

echo "== 体积对比 =="
ls -lh "$OUT"/*.gguf

echo "== 完成后用 Ollama 直接加载验证 =="
cat > "$OUT/Modelfile" <<'MF'
FROM ./model-q4_K_M.gguf
PARAMETER temperature 0.2
PARAMETER num_ctx 8192
MF
echo "  ollama create guanjia-q4 -f $OUT/Modelfile"
echo "  ollama run guanjia-q4 '查一下库存不足的商品'"
echo ""
echo "记录量化前后显存/内存占用："
echo "  macOS: sudo powermetrics --samplers smc -i 1000 -n 1 | grep -i 'GPU'"
echo "  或运行前后各执行一次: /usr/bin/time -l ollama run guanjia-q4 '你好' 2>&1 | grep 'maximum resident'"
