
import os
import shutil

REPLACEMENTS = {
    "OpenCrab": "SeeAgent",
    "opencrab": "seeagent",
    "OPENCRAB": "SEEAGENT"
}

IGNORE_DIRS = {
    ".git", ".venv", "__pycache__", "node_modules", ".idea", ".vscode", "dist", "build", "egg-info"
}

EXTENSIONS_TO_PROCESS = {
    ".py", ".md", ".txt", ".json", ".sh", ".yaml", ".yml", ".toml", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".xml", ".ps1", ".example", ".jsonl", ".vue"
}

def should_process_file(filename):
    if filename == "rename_project.py":
        return False
    return any(filename.endswith(ext) for ext in EXTENSIONS_TO_PROCESS) or filename in ["Dockerfile", "Makefile", "LICENSE", "NOTICE", "VERSION", "requirements.txt", ".env.example", "AGENTS.md"]

def replace_content(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        print(f"Skipping binary file: {file_path}")
        return
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return

    original_content = content
    # Order matters: replace longer/more specific strings first if needed, 
    # but here keys are distinct by case.
    for old, new in REPLACEMENTS.items():
        content = content.replace(old, new)
    
    if content != original_content:
        # print(f"Updating content: {file_path}")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

def rename_path(path):
    dirname, basename = os.path.split(path)
    new_basename = basename
    for old, new in REPLACEMENTS.items():
        if old in new_basename:
            new_basename = new_basename.replace(old, new)
    
    if new_basename != basename:
        new_path = os.path.join(dirname, new_basename)
        print(f"Renaming: {path} -> {new_path}")
        shutil.move(path, new_path)
        return new_path
    return path

def process_directory(root_dir):
    print("Starting Content Replacement...")
    # Phase 1: Content Replacement
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
        # Modify dirnames in-place to skip ignored directories
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        
        for filename in filenames:
            if should_process_file(filename):
                file_path = os.path.join(dirpath, filename)
                replace_content(file_path)

    print("Starting File/Directory Renaming...")
    # Phase 2: Renaming Files and Directories
    # We need to process from bottom up to handle nested renames correctly
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        # Skip ignored directories in the path
        path_parts = dirpath.split(os.sep)
        if any(ignored in path_parts for ignored in IGNORE_DIRS):
            continue

        # Rename files first
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            rename_path(file_path)
        
        # Rename directory itself if needed (except root)
        if dirpath != root_dir:
            rename_path(dirpath)

if __name__ == "__main__":
    current_dir = os.getcwd()
    print(f"Starting replacement in: {current_dir}")
    process_directory(current_dir)
    print("Replacement complete.")
