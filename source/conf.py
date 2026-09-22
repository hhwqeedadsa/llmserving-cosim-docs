project = "LLM Serving 与 MoE-UB 联合仿真"
author = "MoE-UB Co-simulation Project"
copyright = "2026, MoE-UB Co-simulation Project"
release = "0.1"

extensions = [
    "sphinx.ext.todo",
]

templates_path = ["_templates"]
exclude_patterns = []
language = "zh_CN"

html_theme = "furo"
html_title = "LLM Serving / ASTRA-sim / MoE-UB"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_js_files = ["sample_run.js", "explorer.js"]
html_theme_options = {
    "navigation_with_keys": True,
    "top_of_page_button": "edit",
    "source_repository": "https://github.com/hhwqeedadsa/llmserving-cosim-docs/",
    "source_branch": "main",
    "source_directory": "source/",
}

todo_include_todos = True
