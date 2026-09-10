# -*- coding: utf-8 -*-
"""推理服务压测：吞吐量（tokens/s）、首 token 延迟（TTFT）、并发支持。

对任意 OpenAI 兼容服务（Ollama / vLLM / llama.cpp server）压测：
- 非流式：聚合吞吐 = Σ completion_tokens / 总墙钟时间；单请求延迟分布
- 流式  ：TTFT 分布 + 解码吞吐

纯标准库实现。用法示例：
  python scripts/bench_infer.py --api-base http://127.0.0.1:11434/v1 \
      --model qwen2.5:7b-instruct-q4_K_M --concurrency 1,4,8 --n 10
"""
import argparse
import json
import statistics
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROMPT = ("你是商户运营助手。请用中文简要说明如何处理一条外卖差评，"
          "包括回复话术、补偿方案与内部改进措施，150字左右。")


def one_request(api_base, model, max_tokens, stream, timeout):
    """发送单请求。流式返回 (ttft_ms, None, decode_s)；非流式返回 (latency_ms, completion_tokens, None)。"""
    payload = {"model": model, "stream": stream, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": PROMPT}]}
    req = urllib.request.Request(
        api_base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer bench"})
    t0 = time.time()
    ttft = None
    if not stream:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        lat = (time.time() - t0) * 1000
        return lat, data.get("usage", {}).get("completion_tokens", 0), None
    chars = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            delta = obj["choices"][0].get("delta", {}).get("content") or ""
            if delta:
                if ttft is None:
                    ttft = (time.time() - t0) * 1000
                chars += len(delta)
    total = (time.time() - t0) * 1000
    return ttft, chars, (total - (ttft or 0)) / 1000.0


def run_level(api_base, model, n, conc, max_tokens, stream, timeout):
    latencies, ttfts, tokens = [], [], []
    chars_list, decode_s = [], []
    lock = threading.Lock()

    def worker():
        for _ in range(n):
            try:
                if stream:
                    ttft, chars, dec = one_request(api_base, model, max_tokens,
                                                   True, timeout)
                    with lock:
                        if ttft:
                            ttfts.append(ttft)
                        chars_list.append(chars)
                        decode_s.append(dec)
                else:
                    lat, ct, _ = one_request(api_base, model, max_tokens,
                                             False, timeout)
                    with lock:
                        latencies.append(lat)
                        tokens.append(ct or 0)
            except Exception as e:
                with lock:
                    print("  请求失败: {}".format(e))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=conc) as ex:
        list(ex.map(lambda _: worker(), range(conc)))
    wall = time.time() - t0

    def p95(xs):
        return sorted(xs)[max(int(len(xs) * 0.95) - 1, 0)] if xs else 0

    if stream:
        all_chars = sum(chars_list)
        all_decode = sum(d for d in decode_s if d)
        return {
            "concurrency": conc, "requests": conc * n, "mode": "stream",
            "ttft_avg_ms": round(statistics.mean(ttfts), 1) if ttfts else None,
            "ttft_p95_ms": round(p95(ttfts), 1) if ttfts else None,
            "decode_throughput_cps": round(all_chars / all_decode, 1) if all_decode else None,
            "wall_s": round(wall, 2),
        }
    return {
        "concurrency": conc, "requests": conc * n, "mode": "batch",
        "throughput_tok_s": round(sum(tokens) / wall, 1),
        "latency_avg_ms": round(statistics.mean(latencies), 1) if latencies else None,
        "latency_p95_ms": round(p95(latencies), 1) if latencies else None,
        "wall_s": round(wall, 2),
    }


def main():
    ap = argparse.ArgumentParser(description="OpenAI 兼容推理服务压测")
    ap.add_argument("--api-base", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--model", default="qwen2.5:7b-instruct-q4_K_M")
    ap.add_argument("--n", type=int, default=10, help="每并发档请求数")
    ap.add_argument("--concurrency", default="1,4,8", help="并发档位，逗号分隔")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--stream", action="store_true", help="流式模式测 TTFT")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out", default="data/logs/bench_report.json")
    args = ap.parse_args()

    levels = [int(x) for x in args.concurrency.split(",")]
    results = []
    for c in levels:
        print("压测并发={} ...".format(c), flush=True)
        r = run_level(args.api_base, args.model, args.n, c,
                      args.max_tokens, args.stream, args.timeout)
        print(json.dumps(r, ensure_ascii=False))
        results.append(r)
    try:
        import os
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"api_base": args.api_base, "model": args.model,
                       "results": results}, f, ensure_ascii=False, indent=2)
        print("报告已写入", args.out)
    except OSError:
        pass


if __name__ == "__main__":
    main()
