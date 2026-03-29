#!/usr/bin/env python3
"""Convenience entry point — delegates to rtk_client.py."""
import runpy

runpy.run_module("rtk_client", run_name="__main__")
