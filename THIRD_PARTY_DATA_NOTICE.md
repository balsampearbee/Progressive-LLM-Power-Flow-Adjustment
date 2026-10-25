# Third-party data provenance and release review

`src/case39_local.m` contains numerical IEEE 39-bus case data corresponding to a MATPOWER-style `case39` model; the base file was supplied in the research archive and modified for the local power-flow demonstration. Please acknowledge the original New England IEEE39 case and MATPOWER case39 provenance when publishing.

The MATPOWER project states explicitly that the **MATPOWER case data files are not covered by the BSD license that applies to the MATPOWER code**: https://matpower.org/license/. The copyright/license and permissions for redistributed case data must therefore be checked separately.

`src/demo_scenario.json` is synthetic perturbed case data. `fixtures/ieee39.dat` and `.pf` are small parser-test fixtures. The 15,517-bus CSG system is not included.

No license has been assigned to the newly collected code bundle; the authors should select and approve an appropriate license (such as BSD-3-Clause) after checking ownership and third-party obligations. This archive is a **review candidate**, not an already published open-source repository.
