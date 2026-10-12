#include "SovereignMotionPath.h"

#include "CollisionQueryParams.h"
#include "Components/SplineComponent.h"
#include "Engine/World.h"
#include "SovereignMotionTypes.h"

ASovereignMotionPath::ASovereignMotionPath()
{
	PrimaryActorTick.bCanEverTick = false;
	bIsEditorOnlyActor = true;

	Spline = CreateDefaultSubobject<USplineComponent>(TEXT("Spline"));
	RootComponent = Spline;
	Spline->ClearSplinePoints(false);
	Spline->AddSplinePoint(FVector::ZeroVector, ESplineCoordinateSpace::Local, false);
	Spline->AddSplinePoint(FVector(400.f, 0.f, 0.f), ESplineCoordinateSpace::Local, true);
#if WITH_EDITORONLY_DATA
	Spline->EditorUnselectedSplineSegmentColor = FLinearColor(0.3f, 0.8f, 1.f);
#endif
}

FVector ASovereignMotionPath::ProjectToFloor(const UWorld* World, const FVector& Location, const AActor* Ignore)
{
	if (!World)
	{
		return Location;
	}
	FHitResult Hit;
	FCollisionQueryParams Params(SCENE_QUERY_STAT(SovereignMotionFloor), /*bTraceComplex=*/ true, Ignore);
	const FVector Start = Location + FVector(0.f, 0.f, 50.f);
	const FVector End = Location - FVector(0.f, 0.f, 1000.f);
	return World->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params) ? Hit.ImpactPoint : Location;
}

void ASovereignMotionPath::FillRequest(FSovereignMotionRequest& Request) const
{
	const UWorld* World = GetWorld();
	auto Place = [this, World](const FVector& Point)
	{
		return bSnapToFloor ? ProjectToFloor(World, Point, this) : Point;
	};

	const float Length = Spline->GetSplineLength();
	const FVector Start = Spline->GetLocationAtDistanceAlongSpline(0.f, ESplineCoordinateSpace::World);
	const FVector Goal = Spline->GetLocationAtDistanceAlongSpline(Length, ESplineCoordinateSpace::World);
	Request.StartLocation = Place(Start);
	Request.GoalLocation = Place(Goal);

	// Sample the curve evenly; the ends are already the start and goal.
	Request.Waypoints.Reset();
	const int32 Segments = FMath::Max(1, FMath::RoundToInt(Length / FMath::Max(SampleSpacing, 10.f)));
	for (int32 Index = 1; Index < Segments; ++Index)
	{
		const float Distance = Length * float(Index) / float(Segments);
		Request.Waypoints.Add(Place(Spline->GetLocationAtDistanceAlongSpline(Distance, ESplineCoordinateSpace::World)));
	}

	if (bFaceAlongPath)
	{
		Request.StartYawDeg = Spline->GetDirectionAtDistanceAlongSpline(0.f, ESplineCoordinateSpace::World).Rotation().Yaw;
		Request.GoalYawDeg = Spline->GetDirectionAtDistanceAlongSpline(Length, ESplineCoordinateSpace::World).Rotation().Yaw;
	}
	else
	{
		Request.StartYawDeg = StartYawDeg;
		Request.GoalYawDeg = GoalYawDeg;
	}
}
