#pragma once

#include "CoreMinimal.h"
#include "SovereignMotionTypes.h"
#include "UObject/StrongObjectPtr.h"
#include "Widgets/SCompoundWidget.h"

class AActor;
class FStructOnScope;
class UAnimSequence;
class USkeleton;
class IStructureDetailsView;
class STextBlock;

/**
 * The Sovereign Motion Studio tab: edit a motion request, generate it through the
 * sidecar, and bake the result. Requests and results are saved under
 * Saved/SovereignMotion/ so every clip can be traced back to what produced it.
 */
class SSovereignMotionPanel : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SSovereignMotionPanel) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);
	virtual ~SSovereignMotionPanel() override;

private:
	FReply OnCheckSidecar();
	FReply OnUseLevelSelection();
	FReply OnGenerate();
	FReply OnBake();
	FReply OnTogglePreview();
	FText GetPreviewButtonText() const;
	bool CanBake() const { return bHasResult && !bBusy; }
	bool IsIdle() const { return !bBusy; }

	void SetStatus(const FString& Message, bool bError = false);
	FString SaveDirectory() const;

	/** The skeleton the last result should be played or baked on, or null with a reason. */
	USkeleton* ResolveOutputSkeleton(FString& OutError) const;

	/** Spawn or update the in-level preview for LastResult. Returns false with a reason on failure. */
	bool RefreshPreview(FString& OutError);
	void StopPreview();
	bool IsPreviewing() const { return PreviewActor.IsValid(); }

	TSharedPtr<FStructOnScope> RequestScope;
	TSharedPtr<FStructOnScope> BakeScope;
	TSharedPtr<IStructureDetailsView> RequestView;
	TSharedPtr<IStructureDetailsView> BakeView;
	TSharedPtr<STextBlock> StatusText;

	/** Temporary, unsaved actor that plays LastResult in the level. */
	TWeakObjectPtr<AActor> PreviewActor;
	TStrongObjectPtr<UAnimSequence> PreviewSequence;

	FSovereignMotionResult LastResult;
	bool bHasResult = false;
	bool bBusy = false;
};
