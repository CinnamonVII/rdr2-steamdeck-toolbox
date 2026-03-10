#!/bin/bash
set -e

command -v python3 &>/dev/null || { echo "Error: python3 not found"; exit 1; }

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
cd "$SCRIPT_DIR"

echo "Creating Python Virtual Environment..."
python3 -m venv venv

echo "Installing requirements..."
./venv/bin/pip install -r requirements.txt

if ! grep -q "alias rdr2-master" ~/.bashrc; then
    echo "alias rdr2-master='$SCRIPT_DIR/venv/bin/python $SCRIPT_DIR/rdr2_toolbox.py'" >> ~/.bashrc
    echo "Alias 'rdr2-master' added to ~/.bashrc"
    echo "Please run: source ~/.bashrc"
else
    sed -i "s|alias rdr2-master=.*|alias rdr2-master='$SCRIPT_DIR/venv/bin/python $SCRIPT_DIR/rdr2_toolbox.py'|" ~/.bashrc
    echo "Alias 'rdr2-master' updated in ~/.bashrc"
fi

sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python|" "$SCRIPT_DIR/rdr2_toolbox.py"
sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python|" "$SCRIPT_DIR/preview.py"

echo "Setup complete!"
if [ -t 0 ]; then
    read -p "Press Enter to close this window..."
fi

