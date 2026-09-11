# Hardware

This directory contains mechanical design files for the MAGP platform.

## Directory Structure

```
hardware/
├── base_plate/
│   ├── magp_base_plate.f3z   # Fusion 360 source file (editable)
│   ├── magp_base_plate.stp   # STEP file (CAD interchange / import into any CAD tool)
│   └── magp_base_plate.stl   # STL file (ready for 3D printing)
└── carten210_plate/
    └── carten210_plate.stl   # STL file (ready for 3D printing)
```

## Parts

### base\_plate

A mounting plate designed to be 3D-printed and used to fix various components
(Jetson, LiDAR, PCA9685, etc.) onto the Tamiya TT-02 chassis.

**Recommended print settings:**
- Material: PLA or PETG
- Layer height: 0.2 mm
- Infill: 30 %+
- Supports: as needed

### carten210\_plate

A mounting plate designed to be 3D-printed for use with the CarTen M210 chassis.

**Recommended print settings:**
- Material: PLA or PETG
- Layer height: 0.2 mm
- Infill: 30 %+
- Supports: as needed

## License

The hardware designs in this directory are derived from
[OpenMiniCarWorks](https://github.com/Hiroyuki-Okuda/OpenMiniCarWorks)
by Hiroyuki Okuda, which is released into the public domain under the
[Unlicense](https://unlicense.org).

Modifications and additions are Copyright (c) 2025 Atsuta Kazuki, Aiki Kada
and are licensed under the [MIT License](../LICENSE).
