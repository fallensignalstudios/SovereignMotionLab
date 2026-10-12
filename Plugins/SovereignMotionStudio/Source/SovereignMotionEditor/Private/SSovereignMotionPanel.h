#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "SovereignMotionTypes.h"
#include "UObject/StrongObjectPtr.h"
#include "Widgets/SCompoundWidget.h"

class AActor;
class ASovereignMotionPath;
class FStructOnScope;
struct FPropertyChangedEvent;
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
	FReply OnNewPath();
	FReply OnGenerate();
	FReply OnBake();
	FReply OnTogglePreview();
	FText GetPreviewButtonText() const;
	bool CanBake() const { return bHasResult && !bBusy; }
	bool IsIdle() const { return !bBusy; }

	/** Send the current request to the sidecar (re-reading the bound path first, if any). */
	void StartGenerate();

	/** Copy the bound path's start, goal and waypoints into the request. False if no path is bound. */
	bool ApplyBoundPath();

	// Auto-regenerate: editing the bound path schedules a debounced regeneration.
	void OnObjectPropertyChanged(UObject* Object, FPropertyChangedEvent& Event);
	void OnActorMoved(AActor* Actor);
	void ScheduleRegenerate();
	bool OnRegenerateTick(float DeltaTime);

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

	/** Path actor driving the request's start, goal and waypoints. */
	TWeakObjectPtr<ASovereignMotionPath> BoundPath;
	bool bAutoRegenerate = true;
	bool bRegenerateQueued = false;
	FTSTicker::FDelegateHandle RegenerateTicker;
	FDelegateHandle PropertyChangedHandle;
	FDelegateHandle ActorMovedHandle;

	FSovereignMotionResult LastResult;
	bool bHasResult = false;
	bool bBusy = false;
};
