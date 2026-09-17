# Source this file: source scripts/setup_env_macos.sh
export VKD_TOOLING_DIR="${VKD_TOOLING_DIR:-$HOME/.local/share/vkd-risk/tooling}"
export QT_DIR="${QT_DIR:-$HOME/Qt/6.11.0/macos}"
export VKD_DOCUMENT_TOOLS_DIR="${VKD_DOCUMENT_TOOLS_DIR:-$HOME/.local/share/vkd-risk/mamba/envs/documents}"
export PATH="$VKD_TOOLING_DIR/bin:$VKD_DOCUMENT_TOOLS_DIR/bin:$QT_DIR/bin:$HOME/.local/bin:$PATH"
