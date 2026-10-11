#pragma once

#include "CoreMinimal.h"
#include "SovereignMotionTypes.h"
#include "Widgets/SCompoundWidget.h"

class FStructOnScope;
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

private:
	FReply OnCheckSidecar();
	FReply OnGenerate();
	FReply OnBake();
	bool CanBake() const { return bHasResult && !bBusy; }
	bool IsIdle() const { return !bBusy; }

	void SetStatus(const FString& Message, bool bError = false);
	FString SaveDirectory() const;

	TSharedPtr<FStructOnScope> RequestScope;
	TSharedPtr<FStructOnScope> BakeScope;
	TSharedPtr<IStructureDetailsView> RequestView;
	TSharedPtr<IStructureDetailsView> BakeView;
	TSharedPtr<STextBlock> StatusText;

	FSovereignMotionResult LastResult;
	bool bHasResult = false;
	bool bBusy = false;
};
