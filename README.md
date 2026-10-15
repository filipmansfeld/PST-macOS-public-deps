# Official openOMSI 0.3.0 Mac dependency cache

This generic recipe builds public external libraries for the exact official openOMSI 0.3.0 source on standard Apple Silicon and Intel Mac runners. The selected official source declares AGPL-3.0-only; its full licence retains the earlier MIT notice. Dependencies keep their own original licences. The recipe exports only external dependencies with corresponding public sources and licences. It contains no private PST client source, helpers, addons, user content, credentials or simulator executable.

The source, Cargo lock, desktop mimalloc v2 feature, target-filtered dependency metadata, toolchain and original notices are verified before export. This is a new 0.3.0 feature contract, not a relabelled 0.2.50 cache. Consumers still use normal Cargo freshness checks and require a fresh actual simulator build.

Official source: https://github.com/openOMSI-org/openOMSI/tree/ab4d5df594de994ca86ff59a3ba29e63b5ef35d6
