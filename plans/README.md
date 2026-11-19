# Approved plans

Every plan the night bot wrote and an approver approved (gate 1), one file an item:
`<issue>-<slug>.md`. The bot opens each as a draft pull request; `/nightbot approve` on it merges
it here and starts the build, which follows the plan as it stood when it was approved.
