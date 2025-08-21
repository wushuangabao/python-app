import os
import re
import uuid
import html
import ebooklib
from ebooklib import epub
import html2text

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    raise RuntimeError("需要安装 beautifulsoup4: pip install beautifulsoup4")

# ========== 工具函数 ==========

def ensure_directory_exists(directory):
    """确保目录存在，如果不存在则创建。"""
    if not os.path.exists(directory):
        os.makedirs(directory)

def _sanitize_code_text(text: str) -> str:
    """规范代码文本中的换行/空白，尽量不破坏原始结构。"""
    # HTML 实体反转（&amp; -> & 等），并把不间断空格替换为普通空格
    text = html.unescape(text).replace("\xa0", " ")
    # 统一换行到 \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text

def _detect_lang_from_pre(pre_tag) -> str:
    """尝试从 <pre> 或内部 <code> 的 class/属性推断语言，用于 ```lang 栅栏。"""
    candidates = []

    def collect_from(tag):
        if not tag:
            return
        # class 里常见：language-cpp, lang-cpp, cpp, c++
        clz = tag.get("class") or []
        for c in clz:
            candidates.append(c)
        # 常见自定义属性
        for key in ("data-lang", "lang", "language"):
            if tag.get(key):
                candidates.append(tag.get(key))

    collect_from(pre_tag)
    code_child = pre_tag.find("code")
    collect_from(code_child)

    # 归一化一下
    norm = [c.lower().replace("language-", "").replace("lang-", "") for c in candidates]
    # 简单映射
    mapping = {
        "c++": "cpp", "c#": "csharp", "py": "python",
        "js": "javascript", "ts": "typescript",
        "sh": "bash", "shell": "bash"
    }
    for n in norm:
        n = mapping.get(n, n)
        if re.fullmatch(r"[a-z0-9+#\-]+", n):
            return n
    return ""  # 不确定就留空

def _extract_pre_blocks_and_tokenize(soup, debug_dir=None):
    """
    把 <pre> 块替换为占位符，返回占位符与代码内容/语言的列表。
    这样 html2text 不会碰到真正的代码文本。
    """
    records = []  # [{token, text, lang, idx}]
    for idx, pre in enumerate(soup.find_all("pre")):
        lang = _detect_lang_from_pre(pre)
        # 使用 separator='\n' 保证 <br> 等被转为换行
        pre_text = pre.get_text(separator="\n", strip=False)
        pre_text = _sanitize_code_text(pre_text)

        token = f"@@__FENCED_{idx}_{uuid.uuid4().hex}__@@"
        # 调试输出到磁盘
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
            with open(os.path.join(debug_dir, f"pre_{idx:03d}_text.txt"), "w", encoding="utf-8") as f:
                f.write(pre_text)
            with open(os.path.join(debug_dir, f"pre_{idx:03d}_outer.html"), "w", encoding="utf-8") as f:
                f.write(str(pre))

        pre.replace_with(token)
        records.append({"idx": idx, "token": token, "text": pre_text, "lang": lang})
    return records

def _save_images(book, images_folder):
    """把 EPUB 里的图片落盘，并返回 {原始basename: 目标相对路径} 映射。"""
    os.makedirs(images_folder, exist_ok=True)
    mapping = {}
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_IMAGE:
            data = item.get_content()
            basename = os.path.basename(item.get_name())
            out_path = os.path.join(images_folder, basename)
            with open(out_path, "wb") as f:
                f.write(data)
            mapping[basename] = basename  # 仅保存 basename，路径由调用方拼
    return mapping

def _rewrite_img_srcs(soup, img_map, rel_img_dir):
    """用 BeautifulSoup 改写 <img src> 指向我们落盘后的图片位置。"""
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        basename = os.path.basename(src)
        if basename in img_map:
            img["src"] = os.path.join(rel_img_dir, img_map[basename])

# ========== 主函数 ==========

def convert_epub_to_markdown(epub_file_path, debug=True):
    """
    把 EPUB 转为 Markdown（保留代码块换行）。
    - debug=True：在同目录生成一个 .debug 目录，落盘每个 <pre> 的中间状态，方便核查。
    """
    book = epub.read_epub(epub_file_path)

    base_dir = os.path.dirname(epub_file_path)
    base_name = os.path.splitext(os.path.basename(epub_file_path))[0]
    markdown_file_path = os.path.splitext(epub_file_path)[0] + ".md"
    images_folder = os.path.join(base_dir, f"{base_name}_Images")
    debug_dir = os.path.join(base_dir, f"{base_name}.debug") if debug else None

    # 准备图片映射
    img_map = _save_images(book, images_folder)
    rel_images_folder = os.path.relpath(images_folder, base_dir)

    # html2text 配置
    h2t = html2text.HTML2Text()
    h2t.ignore_links = False
    h2t.body_width = 0  # 不自动换行
    # 为了防止它把列表/段落缩进之类改写得太激进，保持默认其它设置

    if debug_dir:
        ensure_directory_exists(debug_dir)

    all_md = []

    # 只处理文档型条目
    for doc_idx, item in enumerate(book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
        html_content = item.get_body_content().decode("utf-8", errors="ignore")

        # Soup 解析
        soup = BeautifulSoup(html_content, "html.parser")

        # 改写图片路径
        _rewrite_img_srcs(soup, img_map, rel_images_folder)

        # 提取并占位所有 <pre>
        pre_records = _extract_pre_blocks_and_tokenize(soup, debug_dir=debug_dir)

        # 记录占位后的 HTML（调试）
        if debug_dir:
            with open(os.path.join(debug_dir, f"doc_{doc_idx:03d}_after_tokenize.html"), "w", encoding="utf-8") as f:
                f.write(str(soup))

        # 其余 HTML -> Markdown
        md_partial = h2t.handle(str(soup))

        # 把占位符回填为 ``` 栅栏代码块
        for rec in pre_records:
            fence_lang = rec["lang"]
            code = rec["text"]
            
            # 保证代码块首尾都有换行，防止与上下文粘连
            fenced = f"```{fence_lang}\n{code}\n```\n"

            # 用 re.escape 确保占位符被正确替换
            md_partial = md_partial.replace(rec["token"], fenced)

            if debug_dir:
                # 简要打印到控制台，方便你快速看问题
                lines_count = code.count('\n') + 1
                sample_code = repr(code[:60])
                print(f"[pre {rec['idx']:03d}] lines={lines_count}, lang='{fence_lang}', sample={sample_code}")
                print("  sample:", repr(code.split("\n")[0][:60]), "...")

        if debug_dir:
            with open(os.path.join(debug_dir, f"doc_{doc_idx:03d}_after_inject.md"), "w", encoding="utf-8") as f:
                f.write(md_partial)

        all_md.append(md_partial.strip() + "\n\n")

    # 合并并保存
    final_md = "".join(all_md)
    with open(markdown_file_path, "w", encoding="utf-8") as f:
        f.write(final_md)

    # 顶层调试文件
    if debug_dir:
        with open(os.path.join(debug_dir, "_FINAL.md"), "w", encoding="utf-8") as f:
            f.write(final_md)

    return markdown_file_path, images_folder

# ========== 使用示例 ==========
if __name__ == "__main__":
    epub_file_path = f"E:\BaiduNetdiskDownload\Book\GameQt.epub"
    md_path, img_dir = convert_epub_to_markdown(epub_file_path, debug=True)
    print(f"Markdown file saved at: {md_path}")
    print(f"Images saved in folder: {img_dir}")
