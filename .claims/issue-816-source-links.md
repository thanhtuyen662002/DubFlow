# Issue #816 One source rule for Bán hàng and Tổng hợp

Owner directive 2026-11-13 08:04 (Release Safety Closure, Stage 1 group 2), Epic #637. Bán hàng and Tổng hợp refuse the same sources (links, junctions, reparse points, traversal), checked on the path itself and again in the worker; real files anywhere the user picks stay allowed.
