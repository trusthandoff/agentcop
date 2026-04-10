#!/bin/bash
# agenthijacks demo runner
# Usage: bash demos/run_demo.sh

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
WHITE='\033[1;37m'
DIM='\033[2m'
RESET='\033[0m'

echo ""
echo -e "${WHITE}┌─────────────────────────────────────┐${RESET}"
echo -e "${WHITE}│         agenthijacks demos           │${RESET}"
echo -e "${WHITE}│                                      │${RESET}"
echo -e "${WHITE}│  youtube.com/@agenthijacks           │${RESET}"
echo -e "${WHITE}└─────────────────────────────────────┘${RESET}"
echo ""

# Install dependencies quietly
echo -e "${DIM}Installing dependencies...${RESET}"
pip install rich --quiet
# Install agentcop from local source if not already installed
if ! python -c "import agentcop" 2>/dev/null; then
    pip install -e . --quiet 2>/dev/null || pip install agentcop --quiet
fi
echo -e "${GREEN}Dependencies ready.${RESET}"
echo ""

echo -e "Pick a demo:"
echo -e "  ${GREEN}1)${RESET} THE SLEEPER          — your agent just changed sides"
echo -e "  ${GREEN}2)${RESET} GHOST IN THE WIRE    — your API keys left the building"
echo -e "  ${GREEN}3)${RESET} THE RANSOMWARE        — your agent just encrypted everything"
echo -e "  ${GREEN}4)${RESET} THE SILENT WORM       — your agent just went viral"
echo -e "  ${GREEN}5)${RESET} THE FLOOD             — your agent just became a weapon"
echo -e "  ${GREEN}6)${RESET} THE AMPLIFIER         — one request, ten thousand responses"
echo -e "  ${GREEN}7)${RESET} All (back to back)"
echo ""
read -rp "Enter choice [1-7]: " choice

run_demo() {
    local script="$1"
    local name="$2"

    echo ""
    echo -e "${GREEN}Starting: ${name}${RESET}"
    echo -e "${DIM}────────────────────────────────────────${RESET}"
    echo ""
    echo -e "To record with asciinema:"
    echo -e "  ${WHITE}asciinema rec output.cast -- python ${script}${RESET}"
    echo ""
    echo -e "Or just screen record now."
    echo -e "${GREEN}Press ENTER when ready...${RESET}"
    read -r

    python "$script"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$choice" in
    1)
        run_demo "${SCRIPT_DIR}/the_sleeper.py" "THE SLEEPER"
        ;;
    2)
        run_demo "${SCRIPT_DIR}/ghost_in_the_wire.py" "GHOST IN THE WIRE"
        ;;
    3)
        run_demo "${SCRIPT_DIR}/the_ransomware.py" "THE RANSOMWARE"
        ;;
    4)
        run_demo "${SCRIPT_DIR}/the_silent_worm.py" "THE SILENT WORM"
        ;;
    5)
        run_demo "${SCRIPT_DIR}/the_flood.py" "THE FLOOD"
        ;;
    6)
        run_demo "${SCRIPT_DIR}/the_amplifier.py" "THE AMPLIFIER"
        ;;
    7)
        for demo in \
            "${SCRIPT_DIR}/the_sleeper.py:THE SLEEPER" \
            "${SCRIPT_DIR}/ghost_in_the_wire.py:GHOST IN THE WIRE" \
            "${SCRIPT_DIR}/the_ransomware.py:THE RANSOMWARE" \
            "${SCRIPT_DIR}/the_silent_worm.py:THE SILENT WORM" \
            "${SCRIPT_DIR}/the_flood.py:THE FLOOD" \
            "${SCRIPT_DIR}/the_amplifier.py:THE AMPLIFIER"
        do
            script="${demo%%:*}"
            name="${demo##*:}"
            run_demo "$script" "$name"
            echo ""
            echo -e "${DIM}Next demo in 3 seconds...${RESET}"
            sleep 3
        done
        ;;
    *)
        echo -e "${RED}Invalid choice.${RESET}"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}Done. ${DIM}pip install agentcop | agentcop.live${RESET}"
