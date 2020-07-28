#!/bin/sh

## (c) 2019 Arjen Balfoort (SolydXK). Based on :
## live-config(7) - System Configuration Scripts
## Copyright (C) 2006-2013 Daniel Baumann <daniel@debian.org>
##
## This program comes with ABSOLUTELY NO WARRANTY; for details see COPYING.
## This is free software, and you are welcome to redistribute it
## under certain conditions; see COPYING for details.
##
## Purpose: disable power manager during live session

if [ -x /usr/bin/xfce4-power-manager ]; then
    xfce4-power-manager -q
fi

xset -dpms
xset s noblank
xset s off

