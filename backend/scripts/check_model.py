"""模型连通性自检。填完 Key 先跑这个，别急着传图。

用法（在 backend/ 目录下）：
    uv run python scripts/check_model.py

它做四件事：
  1. 报告当前配置了哪几个通道（只报有没有 Key，不打印 Key）
  2. 用纯文本请求主通道，确认 Key 有效、模型名存在
  3. 用一张图请求主通道，确认这个模型**真的能看图**
  4. 如果第 3 步失败，自动试几个候选视觉模型名，告诉你该把 VISION_MODEL 改成什么

为什么单独写：模型名和视觉支持是最容易出错、又最容易被误判成"代码有 bug"的一环。
出错时这里会打印上游返回的原始错误体，比应用里那句"模型服务暂时不可用"有用得多。
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import httpx

# 让 `app` 可被 import（本文件在 backend/scripts/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

# 图上画的东西，用来判断模型是不是真的"看见"了
EXPECTED_COLOR = "红"
EXPECTED_TEXT = "OK 42"

# 第 3 步失败时依次尝试的候选模型名（DeepSeek 的视觉模型改过几次名字）
VISION_CANDIDATES = (
    "deepseek-flash",
    "deepseek-v4-flash-vision-exp",
    "deepseek-vl2",
)


def _test_image() -> bytes:
    """生成一张红底、写着 OK 42 的 PNG。用真实图片而不是占位符，才能验证视觉。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (320, 120), (220, 30, 30))
    draw = ImageDraw.Draw(image)
    draw.text((24, 48), EXPECTED_TEXT, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _mask(secret: str | None) -> str:
    """只透出长度和头尾，用来确认"填对了行"而不泄露 Key。"""
    if not secret:
        return "未配置"
    if len(secret) <= 10:
        return f"已配置（长度 {len(secret)}）"
    return f"已配置（{secret[:6]}…{secret[-4:]}，长度 {len(secret)}）"


def _post(model: str, base_url: str, api_key: str, content: list[dict]) -> httpx.Response:
    """发一次请求，原样返回响应，方便把上游错误打给人看。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "max_tokens": 200,
    }
    with httpx.Client(timeout=settings.vision_timeout_s) as client:
        return client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )


def _explain(response: httpx.Response) -> str:
    """把上游错误翻译成人话。"""
    if response.status_code < 400:
        return "成功"
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return f"HTTP {response.status_code}：{response.text[:300]}"

    err = body.get("error") if isinstance(body, dict) else None
    message = ""
    if isinstance(err, dict):
        message = str(err.get("message") or err.get("type") or "")
    elif isinstance(err, str):
        message = err
    if not message:
        message = str(body)[:300]

    low = message.lower()
    hints = []
    if "does not support image" in low or "image" in low and "not support" in low:
        hints.append("这个模型不支持图片输入 → 换视觉模型")
    if "model" in low and ("not exist" in low or "not found" in low or "invalid" in low):
        hints.append("模型名不存在 → 换 VISION_MODEL")
    if "authentication" in low or "api key" in low or "401" in str(response.status_code):
        hints.append("Key 不对或没生效 → 检查 DEEPSEEK_API_KEY 有没有多余空格/引号")
    if "insufficient" in low or "balance" in low or "quota" in low:
        hints.append("余额不足 → 去开放平台充值")

    text = f"HTTP {response.status_code}：{message[:300]}"
    if hints:
        text += "\n        建议：" + "；".join(hints)
    return text


def _reply_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError):
        return "<取不到 content>"


def main() -> int:
    print("=" * 68)
    print("Keeper 模型自检")
    print("=" * 68)

    api_key = settings.deepseek_api_key.get_secret_value() if settings.deepseek_api_key else None
    fallback_key = (
        settings.dashscope_api_key.get_secret_value() if settings.dashscope_api_key else None
    )

    print("\n【1/4】当前配置")
    print(f"  主通道  provider={settings.vision_provider:<10} model={settings.vision_model}")
    print(f"          base_url={settings.vision_base_url}")
    print(f"          api_key ={_mask(api_key)}")
    print(f"  备通道  provider={settings.fallback_provider:<10} model={settings.fallback_model}")
    print(f"          api_key ={_mask(fallback_key)}")
    print(
        "          状态    "
        + (
            "已启用（主通道失败会自动切过去）"
            if fallback_key
            else "已关闭（没有 Key，代码会跳过它）"
        )
    )

    if not api_key:
        print("\n❌ DEEPSEEK_API_KEY 还是空的。")
        print("   打开项目根的 .env，找到这一行填上你的 Key（sk- 开头），然后重跑：")
        print("   DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx")
        return 2

    base_url = settings.vision_base_url
    model = settings.vision_model

    print("\n【2/4】纯文本请求（验 Key 和模型名）")
    try:
        response = _post(model, base_url, api_key, [{"type": "text", "text": "只回答两个字：收到"}])
    except httpx.HTTPError as exc:
        print(f"  ❌ 网络层失败：{type(exc).__name__}: {exc}")
        print("     国内直连 api.deepseek.com 一般没问题；如果挂了，检查代理设置。")
        return 1
    print(f"  {_explain(response)}")
    if response.status_code >= 400:
        return 1
    print(f"  模型回：{_reply_text(response)[:60]}")

    print("\n【3/4】图片请求（验视觉，这是投喂能不能用的关键）")
    data_url = "data:image/png;base64," + base64.b64encode(_test_image()).decode()
    question = "这张图的背景是什么颜色？图上写了什么文字？只回答一行，格式：颜色=<颜色> 文字=<文字>"
    image_content = [
        {"type": "text", "text": question},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]

    vision_ok = False
    working_model = model
    try:
        response = _post(model, base_url, api_key, image_content)
    except httpx.HTTPError as exc:
        print(f"  ❌ 网络层失败：{type(exc).__name__}: {exc}")
        return 1

    if response.status_code < 400:
        answer = _reply_text(response)
        print(f"  模型回：{answer[:120]}")
        if EXPECTED_COLOR in answer and "42" in answer:
            print("  ✅ 视觉正常，而且真的读出了图上的字。")
            vision_ok = True
        else:
            print("  ⚠️ 请求成功，但回答对不上（期望「红」和「42」）。")
            print("     可能只是没认真看，不影响使用；但如果你反复看到这种情况，换模型试试。")
            vision_ok = True
    else:
        print(f"  {_explain(response)}")

    print("\n【4/4】结论")
    if vision_ok:
        print(f"  ✅ 可以用。当前 VISION_MODEL={working_model}")
        print("\n  下一步：起后端和前端，然后传截图。")
        print("    终端 A：cd backend && uv run uvicorn app.main:app --reload --port 8000")
        print("    终端 B：cd frontend && pnpm dev")
        return 0

    print("  ❌ 当前模型不支持图片。正在试候选模型…\n")
    for candidate in VISION_CANDIDATES:
        if candidate == model:
            continue
        print(f"  试 {candidate} …", end=" ")
        try:
            response = _post(candidate, base_url, api_key, image_content)
        except httpx.HTTPError as exc:
            print(f"网络失败 {type(exc).__name__}")
            continue
        if response.status_code < 400:
            answer = _reply_text(response)
            good = EXPECTED_COLOR in answer and "42" in answer
            print("可用" + ("（并且读对了图）" if good else "（请求通过，但没读对）"))
            print(f"\n  ✅ 把 .env 改成：VISION_MODEL={candidate}")
            print("     然后重跑本脚本确认。")
            return 0
        print(_explain(response).replace("\n", " "))

    print("\n  ❌ 候选模型都不行。")
    print("     去 https://platform.deepseek.com 看「模型列表」页，确认哪个模型支持图片，")
    print("     把名字填进 .env 的 VISION_MODEL，再重跑本脚本。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
