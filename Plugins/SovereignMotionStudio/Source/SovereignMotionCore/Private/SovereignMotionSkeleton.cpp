#include "SovereignMotionSkeleton.h"

#include "Animation/Skeleton.h"
#include "ReferenceSkeleton.h"

void SovereignMotionSkeleton::Describe(const USkeleton& Skeleton, TArray<FSovereignMotionJoint>& OutJoints)
{
	const FReferenceSkeleton& RefSkeleton = Skeleton.GetReferenceSkeleton();
	const TArray<FTransform>& RefPose = RefSkeleton.GetRefBonePose();
	const int32 NumBones = RefSkeleton.GetNum();

	// The reference skeleton already orders parents before children, with the root at index 0.
	TArray<FTransform> Component;
	Component.SetNum(NumBones);
	OutJoints.Reset(NumBones);
	for (int32 Bone = 0; Bone < NumBones; ++Bone)
	{
		const int32 Parent = RefSkeleton.GetParentIndex(Bone);
		Component[Bone] = Parent == INDEX_NONE ? RefPose[Bone] : RefPose[Bone] * Component[Parent];

		FSovereignMotionJoint& Joint = OutJoints.AddDefaulted_GetRef();
		Joint.Name = RefSkeleton.GetBoneName(Bone);
		Joint.Parent = Parent;
		Joint.Offset = Parent == INDEX_NONE
			? Component[Bone].GetLocation()
			: Component[Bone].GetLocation() - Component[Parent].GetLocation();
	}
}
