# MLV-App provider-control candidate

This directory is a review candidate against fleet doctrine commit
488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d. Start with ADOPTION-CANDIDATE.md and
CURRENT-SAFETY-EVIDENCE.json.

Local proof:

    py -3 -m unittest tools.provider_control.tests.test_mlv_lane_supervisor -v
    py -3 tools/provider_control/vendor/universal_provider_control.py validate profile tools/provider_control/mlv-project-profile.candidate.json
    py -3 tools/provider_control/vendor/universal_provider_control.py validate inventory tools/provider_control/inventory-post-install.candidate.json
    pwsh -NoProfile -File tools/provider_control/install-mlv-lane-supervisor.ps1

Do not run the installer in Apply mode; it refuses by design. Do not enable the task or open a gate
from this candidate.
