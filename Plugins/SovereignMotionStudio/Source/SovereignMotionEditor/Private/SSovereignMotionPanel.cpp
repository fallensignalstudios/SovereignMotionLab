#include "SSovereignMotionPanel.h"

#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "Animation/SkeletalMeshActor.h"
#include "CollisionQueryParams.h"
#include "Components/SkeletalMeshComponent.h"
#include "DrawDebugHelpers.h"
#include "Editor.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/Selection.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "IDetailsView.h"
#include "IStructureDetailsView.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "PropertyEditorModule.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "SovereignMotionBakeLibrary.h"
#include "SovereignMotionSidecarClient.h"
#include "UObject/StructOnScope.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "SovereignMotionPanel"

void SSovereignMotionPanel::Construct(const FArguments& InArgs)
{
	RequestScope = MakeShared<FStructOnScope>(FSovereignMotionRequest::StaticStruct());
	BakeScope = MakeShared<FStructOnScope>(FSovereignBakeSettings::StaticStruct());

	FPropertyEditorModule& PropertyEditor = FModuleManager::LoadModuleChecked<FPropertyEditorModule>("PropertyEditor");
	FDetailsViewArgs DetailsArgs;
	DetailsArgs.NameAreaSettings = FDetailsViewArgs::HideNameArea;
	DetailsArgs.bAllowSearch = false;
	const FStructureDetailsViewArgs StructArgs;
	RequestView = PropertyEditor.CreateStructureDetailView(DetailsArgs, StructArgs, RequestScope);
	BakeView = PropertyEditor.CreateStructureDetailView(DetailsArgs, StructArgs, BakeScope);

	ChildSlot
	[
		SNew(SVerticalBox)

		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(4.f)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth().Padding(2.f)
			[
				SNew(SButton)
				.Text(LOCTEXT("CheckSidecar", "Check Sidecar"))
				.ToolTipText(LOCTEXT("CheckSidecarTip", "Ping the local Python service (python -m sovereign_motion serve)."))
				.IsEnabled(this, &SSovereignMotionPanel::IsIdle)
				.OnClicked(this, &SSovereignMotionPanel::OnCheckSidecar)
			]
			+ SHorizontalBox::Slot().AutoWidth().Padding(2.f)
			[
				SNew(SButton)
				.Text(LOCTEXT("UseSelection", "Use Level Selection"))
				.ToolTipText(LOCTEXT("UseSelectionTip",
					"Set the path from actors selected in the level, in selection order: first = start, "
					"last = goal, any in between = waypoints. One actor sets just the goal. Points are "
					"dropped onto the floor below each actor; yaw comes from the actor's rotation."))
				.IsEnabled(this, &SSovereignMotionPanel::IsIdle)
				.OnClicked(this, &SSovereignMotionPanel::OnUseLevelSelection)
			]
			+ SHorizontalBox::Slot().AutoWidth().Padding(2.f)
			[
				SNew(SButton)
				.Text(LOCTEXT("Generate", "Generate"))
				.IsEnabled(this, &SSovereignMotionPanel::IsIdle)
				.OnClicked(this, &SSovereignMotionPanel::OnGenerate)
			]
			+ SHorizontalBox::Slot().AutoWidth().Padding(2.f)
			[
				SNew(SButton)
				.Text(this, &SSovereignMotionPanel::GetPreviewButtonText)
				.ToolTipText(LOCTEXT("PreviewTip",
					"Play the generated clip in the level on a temporary mesh, along its path. Nothing is saved. "
					"The viewport must be in Realtime mode for it to animate."))
				.IsEnabled(this, &SSovereignMotionPanel::CanBake)
				.OnClicked(this, &SSovereignMotionPanel::OnTogglePreview)
			]
			+ SHorizontalBox::Slot().AutoWidth().Padding(2.f)
			[
				SNew(SButton)
				.Text(LOCTEXT("Bake", "Bake to Animation Sequence"))
				.IsEnabled(this, &SSovereignMotionPanel::CanBake)
				.OnClicked(this, &SSovereignMotionPanel::OnBake)
			]
		]

		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(6.f, 2.f)
		[
			SAssignNew(StatusText, STextBlock)
			.AutoWrapText(true)
			.Text(LOCTEXT("Ready", "Start the sidecar, edit the request, then Generate."))
		]

		+ SVerticalBox::Slot()
		.FillHeight(1.f)
		[
			SNew(SScrollBox)
			+ SScrollBox::Slot()
			[
				RequestView->GetWidget().ToSharedRef()
			]
			+ SScrollBox::Slot()
			.Padding(0.f, 8.f, 0.f, 0.f)
			[
				BakeView->GetWidget().ToSharedRef()
			]
		]
	];
}

SSovereignMotionPanel::~SSovereignMotionPanel()
{
	StopPreview();
}

void SSovereignMotionPanel::SetStatus(const FString& Message, bool bError)
{
	if (StatusText.IsValid())
	{
		StatusText->SetText(FText::FromString(Message));
		StatusText->SetColorAndOpacity(bError ? FSlateColor(FLinearColor(1.f, 0.35f, 0.3f)) : FSlateColor::UseForeground());
	}
}

FString SSovereignMotionPanel::SaveDirectory() const
{
	return FPaths::ProjectSavedDir() / TEXT("SovereignMotion");
}

FReply SSovereignMotionPanel::OnCheckSidecar()
{
	SetStatus(TEXT("Checking sidecar..."));
	TWeakPtr<SSovereignMotionPanel> WeakThis = StaticCastSharedRef<SSovereignMotionPanel>(AsShared());
	FSovereignMotionSidecarClient().CheckHealth([WeakThis](bool bOk, const FString& Message)
	{
		if (TSharedPtr<SSovereignMotionPanel> This = WeakThis.Pin())
		{
			This->SetStatus(Message, !bOk);
		}
	});
	return FReply::Handled();
}

namespace
{
	/** Drop a point onto whatever is below it, so the root sits on the floor rather than at an actor's pivot. */
	FVector ProjectToFloor(const UWorld* World, const AActor* Actor)
	{
		const FVector Location = Actor->GetActorLocation();
		if (!World)
		{
			return Location;
		}
		FHitResult Hit;
		FCollisionQueryParams Params(SCENE_QUERY_STAT(SovereignMotionFloor), /*bTraceComplex=*/ true, Actor);
		const FVector Start = Location + FVector(0.f, 0.f, 50.f);
		const FVector End = Location - FVector(0.f, 0.f, 1000.f);
		return World->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params) ? Hit.ImpactPoint : Location;
	}
}

FReply SSovereignMotionPanel::OnUseLevelSelection()
{
	TArray<AActor*> Actors;
	if (GEditor)
	{
		// USelection keeps the order actors were selected in.
		for (FSelectionIterator It(*GEditor->GetSelectedActors()); It; ++It)
		{
			if (AActor* Actor = Cast<AActor>(*It))
			{
				Actors.Add(Actor);
			}
		}
	}
	if (Actors.Num() == 0)
	{
		SetStatus(TEXT("Select one or more actors in the level first (e.g. Target Points)."), true);
		return FReply::Handled();
	}

	const UWorld* World = GEditor->GetEditorWorldContext().World();
	FSovereignMotionRequest& Request = *reinterpret_cast<FSovereignMotionRequest*>(RequestScope->GetStructMemory());
	AActor* GoalActor = Actors.Last();
	Request.GoalLocation = ProjectToFloor(World, GoalActor);
	Request.GoalYawDeg = GoalActor->GetActorRotation().Yaw;
	Request.Waypoints.Reset();
	if (Actors.Num() > 1)
	{
		Request.StartLocation = ProjectToFloor(World, Actors[0]);
		Request.StartYawDeg = Actors[0]->GetActorRotation().Yaw;
		for (int32 Index = 1; Index < Actors.Num() - 1; ++Index)
		{
			Request.Waypoints.Add(ProjectToFloor(World, Actors[Index]));
		}
	}
	RequestView->GetDetailsView()->ForceRefresh();

	SetStatus(Actors.Num() == 1
		? FString::Printf(TEXT("Goal set from %s."), *GoalActor->GetActorLabel())
		: FString::Printf(TEXT("Path set from %d actors: start %s, goal %s, %d waypoint(s)."),
			Actors.Num(), *Actors[0]->GetActorLabel(), *GoalActor->GetActorLabel(), Request.Waypoints.Num()));
	return FReply::Handled();
}

FReply SSovereignMotionPanel::OnGenerate()
{
	const FSovereignMotionRequest Request = *reinterpret_cast<const FSovereignMotionRequest*>(RequestScope->GetStructMemory());
	const FString BaseFile = SaveDirectory() / FPaths::MakeValidFileName(Request.RequestId);

	// Save the request first: it is the source of truth for regenerating this clip later.
	FFileHelper::SaveStringToFile(FSovereignMotionSidecarClient::BuildRequestJson(Request), *(BaseFile + TEXT(".request.json")));

	bBusy = true;
	SetStatus(FString::Printf(TEXT("Generating %s via %s..."), *Request.RequestId, *Request.Backend));
	TWeakPtr<SSovereignMotionPanel> WeakThis = StaticCastSharedRef<SSovereignMotionPanel>(AsShared());
	FSovereignMotionSidecarClient().Generate(Request,
		[WeakThis, BaseFile](bool bOk, const FSovereignMotionResult& Result, const FString& RawJson, const FString& Error)
		{
			TSharedPtr<SSovereignMotionPanel> This = WeakThis.Pin();
			if (!This)
			{
				return;
			}
			This->bBusy = false;
			if (!bOk)
			{
				This->SetStatus(FString::Printf(TEXT("Generation failed: %s"), *Error), true);
				return;
			}
			FFileHelper::SaveStringToFile(RawJson, *(BaseFile + TEXT(".result.json")));
			This->LastResult = Result;
			This->bHasResult = true;

			FString Message = FString::Printf(TEXT("Generated %d frames (%.2fs) on skeleton %s via %s. Saved to %s.result.json"),
				Result.Frames.Num(), Result.GetDurationSeconds(), *Result.SkeletonName.ToString(), *Result.Backend, *BaseFile);
			for (const FString& Note : Result.Notes)
			{
				Message += TEXT("\n  - ") + Note;
			}
			if (This->IsPreviewing())
			{
				FString PreviewError;
				if (!This->RefreshPreview(PreviewError))
				{
					This->StopPreview();
					Message += TEXT("\nPreview stopped: ") + PreviewError;
				}
			}
			This->SetStatus(Message);
		});
	return FReply::Handled();
}

USkeleton* SSovereignMotionPanel::ResolveOutputSkeleton(FString& OutError) const
{
	const FSovereignBakeSettings& Settings = *reinterpret_cast<const FSovereignBakeSettings*>(BakeScope->GetStructMemory());
	const FSovereignMotionRequest& Request = *reinterpret_cast<const FSovereignMotionRequest*>(RequestScope->GetStructMemory());
	if (USkeleton* Explicit = Settings.SourceSkeleton.LoadSynchronous())
	{
		return Explicit;
	}
	// Without an explicit source skeleton, use the target skeleton the clip was generated on.
	USkeleton* Target = Request.TargetSkeleton.LoadSynchronous();
	if (Target && Target->GetFName() == LastResult.SkeletonName)
	{
		return Target;
	}
	OutError = FString::Printf(TEXT("The result is on skeleton '%s'. Set Source Skeleton to a skeleton with matching ")
		TEXT("bones, or generate with Target Skeleton set using a backend that supports it."),
		*LastResult.SkeletonName.ToString());
	return nullptr;
}

FReply SSovereignMotionPanel::OnBake()
{
	const FSovereignBakeSettings& Settings = *reinterpret_cast<const FSovereignBakeSettings*>(BakeScope->GetStructMemory());
	FString Message;
	USkeleton* Skeleton = ResolveOutputSkeleton(Message);
	if (!Skeleton)
	{
		SetStatus(Message, true);
		return FReply::Handled();
	}

	UAnimSequence* Sequence = USovereignMotionBakeLibrary::BakeResult(
		LastResult, Skeleton, Settings.PackagePath, Settings.AssetName, Settings.bRelativeToStart, Settings.IKBoneSources, Message);
	SetStatus(Message, Sequence == nullptr);
	if (Sequence && Settings.bOpenAfterBake && GEditor)
	{
		GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()->OpenEditorForAsset(Sequence);
	}
	return FReply::Handled();
}

FText SSovereignMotionPanel::GetPreviewButtonText() const
{
	return IsPreviewing() ? LOCTEXT("StopPreview", "Stop Preview") : LOCTEXT("Preview", "Preview in Level");
}

FReply SSovereignMotionPanel::OnTogglePreview()
{
	if (IsPreviewing())
	{
		StopPreview();
		SetStatus(TEXT("Preview stopped."));
		return FReply::Handled();
	}
	FString Error;
	if (RefreshPreview(Error))
	{
		SetStatus(TEXT("Previewing in the level (looping). If it doesn't move, turn on Realtime in the viewport (Ctrl+R)."));
	}
	else
	{
		StopPreview();
		SetStatus(Error, true);
	}
	return FReply::Handled();
}

bool SSovereignMotionPanel::RefreshPreview(FString& OutError)
{
	UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
	if (!World || !bHasResult)
	{
		OutError = TEXT("Nothing to preview: generate a clip first.");
		return false;
	}
	USkeleton* Skeleton = ResolveOutputSkeleton(OutError);
	if (!Skeleton)
	{
		return false;
	}

	const FSovereignBakeSettings& Settings = *reinterpret_cast<const FSovereignBakeSettings*>(BakeScope->GetStructMemory());
	USkeletalMesh* Mesh = Settings.PreviewMesh.LoadSynchronous();
	if (!Mesh)
	{
		Mesh = Skeleton->GetPreviewMesh(/*bFindIfNotSet=*/ true);
	}
	if (!Mesh)
	{
		OutError = FString::Printf(TEXT("No mesh to preview with: set Preview Mesh, or give %s a preview mesh."), *Skeleton->GetName());
		return false;
	}
	if (Mesh->GetSkeleton() != Skeleton)
	{
		OutError = FString::Printf(TEXT("Preview mesh %s uses skeleton %s, not %s."),
			*Mesh->GetName(), *GetNameSafe(Mesh->GetSkeleton()), *Skeleton->GetName());
		return false;
	}

	UAnimSequence* Sequence = USovereignMotionBakeLibrary::BuildPreviewSequence(LastResult, Skeleton, Settings.IKBoneSources, OutError);
	if (!Sequence)
	{
		return false;
	}
	PreviewSequence.Reset(Sequence);

	ASkeletalMeshActor* Actor = Cast<ASkeletalMeshActor>(PreviewActor.Get());
	if (!Actor)
	{
		// Temporary editor actor: transient (never saved with the level) and kept out of the outliner.
		FActorSpawnParameters Params;
		Params.ObjectFlags |= RF_Transient;
		Params.bTemporaryEditorActor = true;
		Params.bHideFromSceneOutliner = true;
		Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
		// The clip carries level-space root motion, so the actor sits at the world origin.
		Actor = World->SpawnActor<ASkeletalMeshActor>(FVector::ZeroVector, FRotator::ZeroRotator, Params);
		if (!Actor)
		{
			OutError = TEXT("Could not spawn the preview actor.");
			return false;
		}
		Actor->SetActorEnableCollision(false);
		PreviewActor = Actor;
	}

	USkeletalMeshComponent* Component = Actor->GetSkeletalMeshComponent();
	Component->SetSkeletalMeshAsset(Mesh);
	Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Component->SetUpdateAnimationInEditor(true);
	Component->SetAnimationMode(EAnimationMode::AnimationSingleNode);
	Component->PlayAnimation(Sequence, /*bLooping=*/ true);

	// Draw the clip's actual root path (not the request's), with the start and end headings.
	FlushPersistentDebugLines(World);
	const FColor PathColor(80, 200, 255);
	for (int32 Frame = 1; Frame < LastResult.Frames.Num(); ++Frame)
	{
		DrawDebugLine(World, LastResult.Frames[Frame - 1].RootTranslation + FVector(0, 0, 2),
			LastResult.Frames[Frame].RootTranslation + FVector(0, 0, 2), PathColor, /*bPersistentLines=*/ true, -1.f, 0, 1.5f);
	}
	// Mark start (green) and end (red) with the direction of travel. Root yaw would include the mesh's
	// own facing offset, so use the path itself, a few frames in to skip the speed ramps.
	const TArray<FSovereignMotionFrame>& Frames = LastResult.Frames;
	const int32 Last = Frames.Num() - 1;
	const int32 Span = FMath::Min(5, Last);
	struct FPathEnd
	{
		int32 Frame;
		FVector Direction;
		FColor Color;
	};
	const FPathEnd Ends[] = {
		{ 0, (Frames[Span].RootTranslation - Frames[0].RootTranslation).GetSafeNormal2D(), FColor::Green },
		{ Last, (Frames[Last].RootTranslation - Frames[Last - Span].RootTranslation).GetSafeNormal2D(), FColor::Red },
	};
	for (const FPathEnd& End : Ends)
	{
		const FVector Origin = Frames[End.Frame].RootTranslation + FVector(0, 0, 2);
		DrawDebugSphere(World, Origin, 12.f, 12, End.Color, /*bPersistentLines=*/ true);
		if (!End.Direction.IsNearlyZero())
		{
			DrawDebugDirectionalArrow(World, Origin, Origin + End.Direction * 60.f, 25.f, End.Color, true, -1.f, 0, 2.f);
		}
	}
	return true;
}

void SSovereignMotionPanel::StopPreview()
{
	if (AActor* Actor = PreviewActor.Get())
	{
		if (UWorld* World = Actor->GetWorld())
		{
			FlushPersistentDebugLines(World);
			World->EditorDestroyActor(Actor, /*bShouldModifyLevel=*/ false);
		}
	}
	PreviewActor.Reset();
	PreviewSequence.Reset();
}

#undef LOCTEXT_NAMESPACE
