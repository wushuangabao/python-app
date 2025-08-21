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

def translate(text, retries=5, base_delay=1):
    """
    使用 DeepSeek API 翻译文本。

    :param text: 要翻译的文本。
    :param retries: 最大重试次数。
    :param base_delay: 基础重试延迟（秒），将使用指数退避策略。
    :return: 翻译后的文本。
    """
    if not text or not text.strip():
        return text
    
    # 定义要捕获的异常类型
    retriable_errors = (APIConnectionError, APIError, RateLimitError)
    
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "请将以下文本从英文翻译成中文："},
                    {"role": "user", "content": text},
                ],
                stream=False,
                timeout=30  # 添加超时设置
            )
            return response.choices[0].message.content
            
        except retriable_errors as e:
            delay = base_delay * (2 ** attempt)  # 指数退避
            print(f"尝试 {attempt + 1}/{retries} 失败：{e}. {f'{delay}秒后重试...' if attempt < retries - 1 else ''}")
            if attempt < retries - 1:
                time.sleep(delay)
                
        except Exception as e:
            print(f"不可重试的错误: {e}")
            break
    
    print("多次尝试后翻译失败。")
    return text


# 测试正常情况
# result = translate("Hello, how are you? This is a test message.")
# print(f"翻译结果: {result}")

####################################################

# 配置日志记录，日志会以追加模式写入，并包含时间戳、日志级别等信息
logging.basicConfig(level=logging.INFO, filename="translate.log", filemode="a", format="%(asctime)s - %(levelname)s - %(message)s")

def read_file(file_path):
    """读取文件并返回文件内容"""
    if not os.path.exists(file_path):
        logging.error(f"文件 {file_path} 不存在！")
        return []

    with open(file_path, "r", encoding="utf-8") as file:
        return file.readlines()

def write_file(file_path, content):
    """写入文件"""
    with open(file_path, "w", encoding="utf-8") as file:
        for section in content:
            file.write(section)

def translate_lines(lines, retries=5):
    """逐行翻译文本"""
    result_content = []
    skip_translation = False

    for section in lines:
        if not section.strip():  # 跳过空行
            result_content.append(section)
            continue

        # 保留 Markdown 标题、链接、图片和代码块等内容
        if section.startswith('#') or section.startswith('[') or section.startswith('!'):
            result_content.append(section)
            continue

        if "```" in section:  # 切换翻译状态
            skip_translation = not skip_translation
            result_content.append(section)
            continue

        if skip_translation:  # 跳过翻译部分
            result_content.append(section)
            continue

        # 执行翻译
        try:
            translated_text = translate(section.strip())  # 移除行尾空格
            result_content.append(section)
            result_content.append(translated_text + '\n')
        except Exception as e:
            logging.error(f"翻译失败：{e}")
            result_content.append(section)  # 如果翻译失败，保留原文

    return result_content

# 处理文件路径
input_file_path = "GameQt_2.md"
output_file_path_real_translated = "GameQt_3.md"

# 读取文件
lines = read_file(input_file_path)
if not lines:
    logging.error("文件内容为空，无法继续处理。")
else:
    # 执行翻译操作
    result_content = translate_lines(lines)

    # 写入翻译后的文件
    write_file(output_file_path_real_translated, result_content)
    logging.info(f"翻译完成，翻译结果已保存到 {output_file_path_real_translated}")