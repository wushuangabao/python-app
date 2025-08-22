import os
import time
import logging
from dotenv import load_dotenv
from openai import OpenAI
from openai import APIConnectionError, APIError, RateLimitError

API_URL = "https://api.deepseek.com"

# 加载环境变量 .evn
load_dotenv()

# 获取 DeepSeek API 密钥
API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not API_KEY:
    raise ValueError("DEEPSEEK_API_KEY 未在环境变量中找到")

try:
    client = OpenAI(api_key=API_KEY, base_url=API_URL)
except Exception as e:
    raise RuntimeError(f"初始化OpenAI客户端失败: {e}")

# 日志
logging.basicConfig(level=logging.INFO, filename="translate.log", filemode="a",
                    format="%(asctime)s - %(levelname)s - %(message)s")

# 可调参数
BATCH_MAX_LINES = 50          # 单批最大可翻译行数
BATCH_MAX_CHARS = 4000        # 单批最大字符数（粗略防止超长）
REQUEST_TIMEOUT = 30          # 请求超时（秒）
RETRIES = 5
BASE_DELAY = 1

# 可翻译判断：保留 Markdown 标题、链接、图片和代码块等内容
def is_translatable_line(s: str) -> bool:
    if s.startswith("#") or s.startswith("[") or s.startswith("!"):
        return False
    return True

def translate_batch(lines, retries=RETRIES, base_delay=BASE_DELAY):
    """
    将一批行（已确认都需要翻译，且不包含代码块内部的行）一次性发给 API。
    要求模型严格逐行返回翻译，使用特殊分隔符确保对齐。
    """
    if not lines:
        return []

    joined = f"\n".join([l.strip() for l in lines])
    logging.info(f"批量翻译内容（{len(lines)}行，{len(joined)}字符）：\n{joined}")

    system_prompt = (
        "你是专业的中英翻译，尤其擅长Qt和C++技术相关书籍的翻译。任务：将用户输入的英文翻译成简体中文。\n"
        "请逐行一一对应翻译，保持原有分行，不要合并或省略。\n"
        "只返回翻译文本本身；使用完全相同的分隔符将各段翻译连接，以便程序拆分。\n"
        "不要添加解释、编号或额外文本。"
    )

    retriable_errors = (APIConnectionError, APIError, RateLimitError)
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": joined},
                ],
                stream=False,
                timeout=REQUEST_TIMEOUT,
            )
            content = resp.choices[0].message.content

            # 拆分译文
            parts = [p.strip() for p in content.split("\n") if p.strip()]

            logging.info(f"批量翻译结果（{len(parts)}行，{len(content)}字符）：\n{content}")
            
            if len(parts) != len(lines):
                raise ValueError(f"分段数量不匹配：期望 {len(lines)}，实际 {len(parts)}。")
            
            return parts

        except retriable_errors as e:
            delay = base_delay * (2 ** attempt)
            logging.warning(f"批量翻译失败（可重试，第 {attempt+1}/{retries} 次）：{e}，{('将在 %.1f 秒后重试' % delay) if attempt < retries-1 else ''}")
            if attempt < retries - 1:
                time.sleep(delay)

        except ValueError as e:
            logging.error(e)
            return parts

        except Exception as e:
            logging.error(f"批量翻译失败（不可重试）：{e}")
            break

    logging.error("尝试批量翻译失败。")
    return []

def read_file(file_path):
    if not os.path.exists(file_path):
        logging.error(f"文件 {file_path} 不存在！")
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return f.readlines()

def write_file(file_path, content):
    with open(file_path, "w", encoding="utf-8") as f:
        for s in content:
            f.write(s)

def translate_file(input_file_path, output_file_path, append_original=True):
    """
    append_original=True: 原文后追加一行译文
    append_original=False: 仅写入译文，覆盖原文
    """
    lines = read_file(input_file_path)
    if not lines:
        logging.error("文件内容为空，无法继续处理。")
        return

    result = []
    in_code_block = False
    current_batch = []

    def flush_batch():
        nonlocal current_batch, result
        if not current_batch:
            return
        translations = translate_batch(current_batch)
        if append_original:
            # 在对应原文后追加译文
            for en, zh in zip(current_batch, translations):
                result.append(en + "\n")
                result.append(zh + "\n")
            if len(translations) < len(current_batch):
                # 如果翻译结果少于原文行数，补充原文
                for i in range(len(translations), len(current_batch)):
                    result.append(current_batch[i] + "\n")
            elif len(translations) > len(current_batch):
                # 如果翻译结果大于原文行数，追加翻译
                for i in range(len(current_batch), len(translations)):
                    result.append(translations[i] + "\n")
        else:
            # 覆盖原文
            for zh in translations:
                result.append(zh + "\n")
        # 清空批
        current_batch = []

    batch_chars = 0
    batch_lines = 0

    for line in lines:
        # 处理代码块开关
        if "```" in line:
            in_code_block = not in_code_block
            if in_code_block:
                # 进入代码块，清空当前批
                flush_batch()
            # 进入或退出代码块，直接写入当前行
            result.append(line)
            continue

        if in_code_block:
            # 代码块内：原样写入
            result.append(line)
            continue

        stripped = line.strip() # 移除字符串两端的空白字符（包括空格、制表符 \t、换行符 \n 等）
        if not stripped:
            continue # 跳过空行，不输出

        if is_translatable_line(stripped):
            # 检查是否触发批大小限制
            projected_chars = batch_chars + len(stripped)
            projected_lines = batch_lines + 1
            if (projected_lines > BATCH_MAX_LINES) or (projected_chars > BATCH_MAX_CHARS):
                flush_batch()
                batch_chars = 0
                batch_lines = 0

            current_batch.append(stripped)
            batch_chars += (len(stripped) + 1) # +1 预留换行符
            batch_lines += 1
        else:
            # 不翻译的行直接输出；先把已有批 flush
            flush_batch()
            result.append(line)

    # 文件结束，flush 最后一批
    flush_batch()

    write_file(output_file_path, result)
    logging.info(f"翻译完成，翻译结果已保存到 {output_file_path}")

if __name__ == "__main__":
    input_file_path = "GameQt_2.md"
    output_file_path = "GameQt_4.md"
    translate_file(input_file_path, output_file_path, append_original=True)