# Install Trips Local AI for Lightroom

This branch is the Lightroom Classic edition. The full install, Lightroom read-back, cull smart-collection setup, update and uninstall steps are in **[INSTALL-LR.md](INSTALL-LR.md)**.

```bash
./install.sh
```

It installs into `~/localai-lr`, serves the panel at http://127.0.0.1:8766/, and uses the LaunchAgent `com.trips.localai-lr.gui`. It does not touch a Capture One install in `~/localai`.
