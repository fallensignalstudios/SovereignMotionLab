#pragma once

#include "CoreMinimal.h"
#include "SovereignMotionTypes.h"

class USkeleton;

namespace SovereignMotionSkeleton
{
	/**
	 * Describe a skeleton's reference pose in the wire convention: joint 0 is the root, parents come
	 * before children, and offsets are component-space deltas (an identity-rotation bind pose).
	 */
	SOVEREIGNMOTIONCORE_API void Describe(const USkeleton& Skeleton, TArray<FSovereignMotionJoint>& OutJoints);
}
