import os
import frontmatter

def load_markdown_file(file_name):
    base_dir = os.path.dirname(__file__)
    file_path = os.path.join(base_dir, file_name)

    content = ''

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    return content

architect_prompt = load_markdown_file("architect.md")

if __name__ == "__main__":
    pass

