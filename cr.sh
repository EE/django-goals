#!/bin/bash
set -euo pipefail

# Configuration
REPO_URL="." # Assuming the current directory is the git repo
CONFIG_FILE="cr.toml"
SESSION_FILE="cr.sqlite"
TMP_DIR=$(mktemp -d)

# Cleanup function
cleanup() {
    echo "Cleaning up workers and temporary directories..."

    # Send SIGTERM to all child processes
    kill $(jobs -p)

    # Wait for all child processes to exit
    wait

    # Remove the temporary directory
    echo "Removing temporary directory $TMP_DIR"
    rm -rf "$TMP_DIR"
}

# Set up trap to ensure cleanup on script exit
trap cleanup EXIT

# Function to read worker URLs from config file
read_worker_urls() {
    awk '/worker-urls/,/]/' "$CONFIG_FILE" | grep "http" | sed 's/[",]//g' | awk '{print $1}'
}

# Function to start a worker
start_worker() {
    local worker_id=$1
    local url=$2
    local port=$(echo "$url" | awk -F: '{print $NF}')
    local worker_dir="$TMP_DIR/$port"

    echo "Starting worker $worker_id on $url in $worker_dir"

    # Create worker directory and clone the repository
    mkdir -p "$worker_dir"
    git clone "$REPO_URL" "$worker_dir"

    # configure
    cp .env "$worker_dir/.env"
    sed -i "s/\(DATABASE_URL=.*\)/\1-$worker_id/" "$worker_dir/.env"

    # Start the worker in the background
    (
        cd "$worker_dir"
        cosmic-ray --verbosity INFO http-worker --port "$port"
    ) &
}

# Read worker URLs and start workers
worker_urls=$(read_worker_urls)
worker_count=0
for url in $worker_urls; do
    worker_count=$((worker_count + 1))
    start_worker $worker_count $url
done

echo "Started $worker_count workers"

# Run Cosmic Ray
cosmic-ray init "$CONFIG_FILE" "$SESSION_FILE"
cosmic-ray --verbosity=INFO baseline "$CONFIG_FILE"
cosmic-ray exec "$CONFIG_FILE" "$SESSION_FILE"
cr-html "$SESSION_FILE" > cr.html
cr-report "$SESSION_FILE" | tail -n3 > cr-summary.txt
