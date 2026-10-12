#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SovereignMotionTypes.h"
#include "SovereignMotionPath.generated.h"

class USplineComponent;

/**
 * An editable motion path. Select it and drag its spline points in the viewport (Alt-drag a point to
 * add one, right-click for more). The first point is the start, the last is the goal, and the curve
 * between them is sampled into waypoints. Editor-only: stripped from cooked builds.
 */
UCLASS(meta = (DisplayName = "Sovereign Motion Path"))
class SOVEREIGNMOTIONCORE_API ASovereignMotionPath : public AActor
{
	GENERATED_BODY()

public:
	ASovereignMotionPath();

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Path")
	TObjectPtr<USplineComponent> Spline;

	/** Distance between sampled waypoints along the curve. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Path", meta = (ClampMin = "10", Units = "cm"))
	float SampleSpacing = 50.f;

	/** Drop sampled points onto the floor below them, so the root stays on the ground. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Path")
	bool bSnapToFloor = true;

	/** Face along the path at the start and goal. Turn off to set the yaws explicitly. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Path")
	bool bFaceAlongPath = true;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Path", meta = (EditCondition = "!bFaceAlongPath"))
	float StartYawDeg = 0.f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Path", meta = (EditCondition = "!bFaceAlongPath"))
	float GoalYawDeg = 0.f;

	/** Write start, goal and waypoints (world space) into a request. */
	UFUNCTION(BlueprintCallable, Category = "Sovereign Motion")
	void FillRequest(UPARAM(ref) FSovereignMotionRequest& Request) const;

	/** Trace down from a point to the floor below it (or return it unchanged if nothing is hit). */
	static FVector ProjectToFloor(const UWorld* World, const FVector& Location, const AActor* Ignore = nullptr);
};
