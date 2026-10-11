// JSON wire format for the sidecar. Field names are snake_case to match the schema files,
// so this is written by hand rather than with FJsonObjectConverter.

#pragma once

#include "CoreMinimal.h"
#include "SovereignMotionTypes.h"

namespace SovereignMotionJson
{
	inline constexpr const TCHAR* SchemaVersion = TEXT("1.0");

	/** TargetJoints, if given, is sent as character.skeleton (see SovereignMotionSkeleton::Describe). */
	SOVEREIGNMOTIONCORE_API FString RequestToJson(const FSovereignMotionRequest& Request,
		FName TargetSkeletonName = NAME_None, const TArray<FSovereignMotionJoint>* TargetJoints = nullptr);

	/** Returns false and fills OutError if the JSON is not a valid 1.0 result. */
	SOVEREIGNMOTIONCORE_API bool ResultFromJson(const FString& Json, FSovereignMotionResult& OutResult, FString& OutError);
}
