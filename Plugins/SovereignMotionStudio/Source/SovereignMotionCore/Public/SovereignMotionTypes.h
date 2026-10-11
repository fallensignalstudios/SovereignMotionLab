// Mirrors schema/motion_request.schema.json and schema/motion_result.schema.json (version 1.0).
// Unreal conventions throughout: centimeters, Z-up, degrees, seconds.

#pragma once

#include "CoreMinimal.h"
#include "SovereignMotionTypes.generated.h"

class USkeleton;

USTRUCT(BlueprintType)
struct SOVEREIGNMOTIONCORE_API FSovereignJointTarget
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Joint Target")
	FName Joint;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Joint Target", meta = (ClampMin = "0", Units = "s"))
	float TimeS = 0.f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Joint Target", meta = (InlineEditConditionToggle))
	bool bUseLocation = true;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Joint Target", meta = (EditCondition = "bUseLocation"))
	FVector Location = FVector::ZeroVector;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Joint Target", meta = (InlineEditConditionToggle))
	bool bUseRotation = false;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Joint Target", meta = (EditCondition = "bUseRotation"))
	FRotator Rotation = FRotator::ZeroRotator;

	/** Hard targets are re-enforced with IK after generation; soft targets are hints to the model. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Joint Target")
	bool bHard = false;
};

USTRUCT(BlueprintType)
struct SOVEREIGNMOTIONCORE_API FSovereignMotionRequest
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Request")
	FString RequestId = TEXT("motion_001");

	/** Sidecar backend name, e.g. "mock" or "motionbricks". */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Request")
	FString Backend = TEXT("mock");

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Request")
	int32 Seed = 0;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Request", meta = (ClampMin = "1", ClampMax = "240"))
	int32 Fps = 30;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Character")
	FName SkeletonId = TEXT("SK_DominionSoldier");

	/**
	 * Skeleton to generate on. Its reference pose is sent with the request; backends that support
	 * it (the mock today) animate it directly, so the result bakes onto it with no retargeting.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Character")
	TSoftObjectPtr<USkeleton> TargetSkeleton;

	/** Character profile in sidecar/profiles/ (e.g. "dominion_heavy"). Empty for none. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Character")
	FString Profile;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Start")
	FVector StartLocation = FVector::ZeroVector;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Start")
	float StartYawDeg = 0.f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Goal")
	FVector GoalLocation = FVector(500.f, 0.f, 0.f);

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Goal")
	float GoalYawDeg = 0.f;

	/** Arrival deadline in seconds. 0 lets the backend choose timing from speed. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Goal", meta = (ClampMin = "0", Units = "s"))
	float ArriveByS = 0.f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Trajectory")
	TArray<FVector> Waypoints;

	/** idle, walk, jog, run or crouch_walk. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Locomotion")
	FString LocomotionStyle = TEXT("walk");

	/** Speed in cm/s. 0 uses the style default (after profile scaling). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Locomotion", meta = (ClampMin = "0"))
	float SpeedCmS = 0.f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Constraints")
	TArray<FSovereignJointTarget> JointTargets;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Constraints", meta = (MultiLine = true))
	FString TextPrompt;

	/** Pin planted feet with IK after generation, using the clip's foot contacts. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Post-Process")
	bool bFootLock = true;

	/** Recorded with the request for traceability; backends ignore these unless mapped. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Gameplay Context")
	TArray<FString> GameplayTags;
};

USTRUCT(BlueprintType)
struct SOVEREIGNMOTIONCORE_API FSovereignMotionJoint
{
	GENERATED_BODY()

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Joint")
	FName Name;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Joint")
	int32 Parent = INDEX_NONE;

	/** Bind-pose local translation from the parent. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Joint")
	FVector Offset = FVector::ZeroVector;
};

USTRUCT(BlueprintType)
struct SOVEREIGNMOTIONCORE_API FSovereignMotionFrame
{
	GENERATED_BODY()

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Frame")
	FVector RootTranslation = FVector::ZeroVector;

	/** Joint-local rotations, same order as the result's joints. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Frame")
	TArray<FQuat> Rotations;
};

USTRUCT(BlueprintType)
struct SOVEREIGNMOTIONCORE_API FSovereignMotionResult
{
	GENERATED_BODY()

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	FString RequestId;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	FString Backend;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	int32 Fps = 30;

	/** The backend's own skeleton. Retargeting onto game skeletons happens after baking. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	FName SkeletonName;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	TArray<FSovereignMotionJoint> Joints;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	TArray<FSovereignMotionFrame> Frames;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	TArray<bool> LeftFootContacts;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	TArray<bool> RightFootContacts;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	TArray<FString> Notes;

	float GetDurationSeconds() const
	{
		return (Frames.Num() > 1 && Fps > 0) ? float(Frames.Num() - 1) / float(Fps) : 0.f;
	}
};
