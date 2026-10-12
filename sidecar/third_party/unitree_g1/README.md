# Unitree G1 kinematic model

`g1_29dof.xml` is copied unchanged from NVIDIA GR00T-WholeBodyControl
(`motionbricks/assets/skeletons/g1/g1_29dof.xml`, commit `b042411f`), licensed under
Apache 2.0 (see that repository's LICENSE).

It is used only for its kinematic tree, in tests of the G1 -> result conversion and the
G1 -> Mannequin retarget. Mesh references are stripped at load time, so the meshes are not needed.
At runtime the MotionBricks backend reads the model from your own clone instead.
