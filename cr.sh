set -euo pipefail

cosmic-ray init cr.toml cr.sqlite
cosmic-ray --verbosity=INFO baseline cr.toml
cosmic-ray exec cr.toml cr.sqlite
cr-html cr.sqlite > cr.html
cr-report cr.sqlite | tail -n3 > cr-summary.txt
