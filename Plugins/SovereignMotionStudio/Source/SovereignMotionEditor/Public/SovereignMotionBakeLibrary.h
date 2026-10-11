#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "SovereignMotionTypes.h"
#include "SovereignMotionBakeLibrary.generated.h"

class UAnimSequence;
class USkeleton;

USTRUCT(BlueprintType)
struct SOVEREIGNMOTIONEDITOR_API FSovereignBakeSettings
{
	GENERATED_BODY()

	/**
	 * Skeleton whose bone names match the result's skeleton. Leave empty to use the request's
	 * Target Skeleton. For backends that output their own skeleton, bake onto a matching skeleton
	 * first, then use an IK Retargeter to move the clip onto game characters.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Bake")
	TSoftObjectPtr<USkeleton> SourceSkeleton;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Bake", meta = (ContentDir))
	FString PackagePath = TEXT("/Game/SovereignMotion");

	/** Leave empty to use AS_<RequestId>. An existing asset is never overwritten; a suffix is added. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Bake")
	FString AssetName;

	/**
	 * Move the clip so it starts at the origin facing the mesh's forward direction, and enable root
	 * motion. Turn off to keep the level-space positions (useful for previewing in place).
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Bake")
	bool bRelativeToStart = true;

	/** Open the new Animation Sequence in the animation editor after baking. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Bake")
	bool bOpenAfterBake = true;
};

UCLASS()
class SOVEREIGNMOTIONEDITOR_API USovereignMotionBakeLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Write a generated clip into a new Animation Sequence. Foot contacts are stored as
	 * float curves (FootContact_L / FootContact_R) for a later foot-locking pass.
	 * Returns null and explains why in OutMessage on failure; OutMessage also carries warnings on success.
	 */
	UFUNCTION(BlueprintCallable, Category = "Sovereign Motion")
	static UAnimSequence* BakeResult(const FSovereignMotionResult& Result, USkeleton* Skeleton,
		const FString& PackagePath, const FString& AssetName, bool bRelativeToStart, FString& OutMessage);

	/** Load a result previously saved by the studio panel or the sidecar CLI (.json). */
	UFUNCTION(BlueprintCallable, Category = "Sovereign Motion")
	static bool LoadResultFromFile(const FString& FilePath, FSovereignMotionResult& OutResult, FString& OutError);
};
