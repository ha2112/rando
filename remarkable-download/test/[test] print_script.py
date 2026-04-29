from pathlib import Path

graph_of_agent_RM = "graph_of_agent_rm"
nhap_rm = "nhap_rm"

nhap_uuid = "ac6c6386-7180-4d1e-aa5a-409c47135a3d"
graph_of_agent_uuid = "c08b42a6-5be9-4517-9d63-38ae279538c2"  # Change to your target UUID

# Destination directory is relative to the script location, NO home path hardcoded
destination = Path(__file__).resolve().parent.parent / "remarkable-download" / nhap_rm

mode = "hotspot"  # hotspot | home | usb
uuid = nhap_uuid

# Use str(destination) as the target; will not expand to a home directory
script = f"""
mkdir -p "{destination}" && \\
scp -r "remarkable-{mode}:/home/root/.local/share/remarkable/xochitl/{uuid}" "{destination}/" && \\
scp "remarkable-{mode}:/home/root/.local/share/remarkable/xochitl/{uuid}.*" "{destination}/" && \\
scp -r "remarkable-{mode}:/home/root/.local/share/remarkable/xochitl/{uuid}.thumbnails" "{destination}/" && \\
scp -r "remarkable-{mode}:/home/root/.local/share/remarkable/xochitl/{uuid}.textconversion" "{destination}/"
"""

print(script)