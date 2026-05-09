from pathlib import Path

graph_of_agent_RM = "graph_of_agent_rm"
nhap_rm = "nhap_rm"
how_to_paper = "how_to_paper"

how_to_paper_uuid = "2b683e50-82bf-425d-95a2-21dd7909c84f"
nhap_uuid = "ac6c6386-7180-4d1e-aa5a-409c47135a3d"
graph_of_agent_uuid = "c08b42a6-5be9-4517-9d63-38ae279538c2" 
test_trigger_uuid = "3eb07d71-45a5-427a-a0ef-8981489092a2"

# Destination directory is relative to the script location, NO home path hardcoded
destination = Path(__file__).resolve().parent.parent / "test_data" / how_to_paper

mode = "hotspot"  # hotspot | home | usb
uuid = test_trigger_uuid

# Use str(destination) as the target; will not expand to a home directory
script = f"""
mkdir -p "{destination}" && \\
scp -r "remarkable-{mode}:/home/root/.local/share/remarkable/xochitl/{uuid}" "{destination}/" && \\
scp "remarkable-{mode}:/home/root/.local/share/remarkable/xochitl/{uuid}.*" "{destination}/" && \\
scp -r "remarkable-{mode}:/home/root/.local/share/remarkable/xochitl/{uuid}.thumbnails" "{destination}/" && \\
scp -r "remarkable-{mode}:/home/root/.local/share/remarkable/xochitl/{uuid}.textconversion" "{destination}/"
"""

print(script)