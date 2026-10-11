#include "SSovereignMotionPanel.h"

#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "CollisionQueryParams.h"
#include "Editor.h"
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
			This->SetStatus(Message);
		});
	return FReply::Handled();
}

FReply SSovereignMotionPanel::OnBake()
{
	const FSovereignBakeSettings& Settings = *reinterpret_cast<const FSovereignBakeSettings*>(BakeScope->GetStructMemory());
	const FSovereignMotionRequest& Request = *reinterpret_cast<const FSovereignMotionRequest*>(RequestScope->GetStructMemory());

	USkeleton* Skeleton = Settings.SourceSkeleton.LoadSynchronous();
	if (!Skeleton)
	{
		// Without an explicit source skeleton, bake onto the target skeleton the clip was generated on.
		USkeleton* Target = Request.TargetSkeleton.LoadSynchronous();
		if (Target && Target->GetFName() == LastResult.SkeletonName)
		{
			Skeleton = Target;
		}
		else
		{
			SetStatus(FString::Printf(TEXT("The result is on skeleton '%s'. Set Source Skeleton to a skeleton with matching ")
				TEXT("bones, or generate with Target Skeleton set using a backend that supports it."),
				*LastResult.SkeletonName.ToString()), true);
			return FReply::Handled();
		}
	}

	FString Message;
	UAnimSequence* Sequence = USovereignMotionBakeLibrary::BakeResult(
		LastResult, Skeleton, Settings.PackagePath, Settings.AssetName, Settings.bRelativeToStart, Message);
	SetStatus(Message, Sequence == nullptr);
	if (Sequence && Settings.bOpenAfterBake && GEditor)
	{
		GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()->OpenEditorForAsset(Sequence);
	}
	return FReply::Handled();
}

#undef LOCTEXT_NAMESPACE
