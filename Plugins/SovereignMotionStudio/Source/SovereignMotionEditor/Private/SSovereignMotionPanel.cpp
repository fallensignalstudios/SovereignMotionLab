#include "SSovereignMotionPanel.h"

#include "Animation/Skeleton.h"
#include "IStructureDetailsView.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "PropertyEditorModule.h"
#include "SovereignMotionBakeLibrary.h"
#include "SovereignMotionJson.h"
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

FReply SSovereignMotionPanel::OnGenerate()
{
	const FSovereignMotionRequest Request = *reinterpret_cast<const FSovereignMotionRequest*>(RequestScope->GetStructMemory());
	const FString BaseFile = SaveDirectory() / FPaths::MakeValidFileName(Request.RequestId);

	// Save the request first: it is the source of truth for regenerating this clip later.
	FFileHelper::SaveStringToFile(SovereignMotionJson::RequestToJson(Request), *(BaseFile + TEXT(".request.json")));

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
	USkeleton* Skeleton = Settings.SourceSkeleton.LoadSynchronous();
	FString Message;
	const UAnimSequence* Sequence = USovereignMotionBakeLibrary::BakeResult(
		LastResult, Skeleton, Settings.PackagePath, Settings.AssetName, Message);
	SetStatus(Message, Sequence == nullptr);
	return FReply::Handled();
}

#undef LOCTEXT_NAMESPACE
