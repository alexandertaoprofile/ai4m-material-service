"""LLM streaming helpers shared by material workflow stages."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from alpha.logs import logger


async def stream_llm_response(
    llm,
    messages,
    websocket=None,
    mirror_to_content: bool = False,
    mirror_step_id: str = "",
    logger_obj: Optional[Any] = None,
) -> str:
    import httpcore
    import httpx
    import openai

    log = logger_obj or logger

    collected_chunks = []
    retries = 0
    max_retries = 3
    stream_res = None

    # ===== 1) 先获取流（带重试）=====
    while retries < max_retries:
        try:
            # 如果 llm 支持显式 stream 参数，可加上 stream=True
            stream_res = await llm.acompletion_text(messages, timeout=30)
            break
        except (openai.APITimeoutError, httpcore.ReadTimeout, httpx.ReadTimeout) as e:
            retries += 1
            log.warning(f"[LLM_Stream-LOG] 请求超时，重试 {retries}/{max_retries}: {type(e).__name__}")
            await asyncio.sleep(1.0 * retries)
        except Exception as e:
            log.exception(f"[LLM_Stream-LOG] LLM 请求异常: {e!s}")
            if retries < max_retries - 1:
                retries += 1
                await asyncio.sleep(0.5)
                continue
            raise

    if stream_res is None:
        log.error("[LLM_Stream-LOG] 达到最大重试次数，未获得 LLM 响应")
        raise TimeoutError("LLM 请求超时，已放弃重试")

    # ===== 2) 逐 chunk 读取（兼容 3.10-，使用 wait_for 包装 __anext__）=====
    chunk_timeout = 30.0  # 每个 chunk 的超时时间（秒）
    max_total_chars = 2_000_000  # 安全阈值，防止意外的无限流
    total_chars = 0

    ait = stream_res.__aiter__()  # 显式拿到异步迭代器
    log.info("[LLM_Stream-LOG] 开始流式读取...")

    mirror_started = False
    step_id = str(mirror_step_id or "").strip()
    mirror_enabled = bool(mirror_to_content and step_id)

    if mirror_enabled and websocket and websocket.client_state.name == "CONNECTED":
        await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
        mirror_started = True

    try:
        while True:
            try:
                # Python 3.10 及以下用 wait_for + __anext__ 实现“按 chunk 超时”
                chunk = await asyncio.wait_for(ait.__anext__(), timeout=chunk_timeout)
            except asyncio.TimeoutError:
                log.error("[LLM_Stream-LOG] 流式读取超时（等待下一个 chunk 超过限制）")
                if websocket and websocket.client_state.name == "CONNECTED":
                    await websocket.send_text("\n❗ 大模型响应超时，已收集部分结果。\n")
                return "".join(collected_chunks)
            except StopAsyncIteration:
                # 正常结束
                break

            # 解析内容（按 OpenAI Chat Completions 风格）
            chunk_msg = ""
            try:
                if getattr(chunk, "choices", None):
                    choice0 = chunk.choices[0]
                    delta = getattr(choice0, "delta", None)
                    if delta:
                        chunk_msg = getattr(delta, "content", "") or ""
            except Exception as parse_e:
                log.exception(f"[LLM_Stream-LOG] 解析 chunk 异常: {parse_e!s}")

            if chunk_msg:
                collected_chunks.append(chunk_msg)
                total_chars += len(chunk_msg)

                if websocket and websocket.client_state.name == "CONNECTED":
                    await websocket.send_text(chunk_msg)
                elif websocket:
                    log.warning("[LLM_Stream-LOG] WebSocket 已关闭，终止发送")
                    break

                # 防御性上限
                if total_chars >= max_total_chars:
                    log.warning("[LLM_Stream-LOG] 达到最大输出字符上限，终止流式读取")
                    break

    except (httpcore.ReadTimeout, httpx.ReadTimeout) as e:
        log.exception(f"[LLM_Stream-LOG] 网络读取超时: {e!s}")
        if websocket and websocket.client_state.name == "CONNECTED":
            await websocket.send_text("\n❗ 网络连接超时，已收集部分结果。\n")
        return "".join(collected_chunks)
    except Exception as e:
        log.exception(f"[LLM_Stream-LOG] LLM Stream 异常: {e!s}")
        if websocket and websocket.client_state.name == "CONNECTED":
            await websocket.send_text("\n❗ 大模型响应异常，已终止流式传输。\n")
        raise
    finally:
        if mirror_started and websocket and websocket.client_state.name == "CONNECTED":
            try:
                await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
            except Exception:
                pass
        # 尽可能优雅关闭流
        try:
            aclose = getattr(stream_res, "aclose", None)
            if callable(aclose):
                await aclose()
        except Exception as e:
            log.debug(f"[LLM_Stream-LOG] 关闭流时发生异常: {e!s}")

    log.info(f"[LLM_Stream-LOG] 收集到 {len(collected_chunks)} 段输出，总长 {sum(len(c) for c in collected_chunks)} 字符")
    return "".join(collected_chunks)
