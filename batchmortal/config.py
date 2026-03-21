import os
import sys

def load_config(config_path: str = None) -> dict:
    """
    Load configuration from a YAML or TOML file.
    If config_path is not provided, looks for config.yaml or config.toml in the current directory.
    Returns a dictionary of the configuration.
    """
    if config_path is None:
        if os.path.exists("config.yaml"):
            config_path = "config.yaml"
        elif os.path.exists("config.yml"):
            config_path = "config.yml"
        elif os.path.exists("config.toml"):
            config_path = "config.toml"
        else:
            return {}

    if not os.path.exists(config_path):
        print(f"警告：找不到配置文件 '{config_path}'", file=sys.stderr)
        return {}

    ext = os.path.splitext(config_path)[1].lower()

    if ext in (".yaml", ".yml"):
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            print("错误：解析 YAML 配置文件需要安装 PyYAML。", file=sys.stderr)
            print("请运行: pip install pyyaml", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"解析 YAML 配置文件出错：{e}", file=sys.stderr)
            return {}

    elif ext == ".toml":
        try:
            if sys.version_info >= (3, 11):
                import tomllib
                with open(config_path, "rb") as f:
                    return tomllib.load(f)
            else:
                import tomli
                with open(config_path, "rb") as f:
                    return tomli.load(f)
        except ImportError:
            print("错误：解析 TOML 配置文件需要安装 tomli（对于 Python < 3.11）。", file=sys.stderr)
            print("请运行: pip install tomli", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"解析 TOML 配置文件出错：{e}", file=sys.stderr)
            return {}

    else:
        print(f"警告：不支持的配置文件格式 '{ext}'", file=sys.stderr)
        return {}
