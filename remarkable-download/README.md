# reMarkable Download Tools

This folder contains utilities and scripts for downloading files from a reMarkable tablet to your computer.

## Purpose

If you want to backup, transfer, or export notes, PDFs, or templates from your reMarkable device, these scripts can help automate the process.

## Features

- Download all documents and notebooks from your reMarkable.
- Fetch custom templates, PDFs, or other files. (Currently not implemented)
- Helpful for backups or migration to a new device.

## Getting Started

1. **Connect your reMarkable to Wi-Fi** and make a note of its IP address (find this on the tablet under Settings > Wi-Fi).
2. **Enable SSH Access:** The default login for reMarkable tablets is:
   - Username: `root`
   - Password: [something something something]
3. **Run the download script(s):**  
   Refer to the specific scripts in this folder for usage instructions.

Example terminal usage:

```bash
python workflow/main.py
```

## Notes

- Ensure you have set up an SSH alias for connecting to your reMarkable. For details, check `config.py` and `client.py`.
- Make sure your computer and reMarkable are on the same network.
- Be careful with your data! Always keep backups in a safe location.
- Some scripts may require Python or other dependencies; see the individual files for details.

## Contributing

Feel free to open issues or pull requests for improvements, bug fixes, or new features.

## License

See the `LICENSE` file for license information if provided.
