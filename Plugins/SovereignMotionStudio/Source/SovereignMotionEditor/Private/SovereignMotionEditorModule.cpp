#include "Framework/Application/SlateApplication.h"
#include "Framework/Docking/TabManager.h"
#include "Modules/ModuleManager.h"
#include "SSovereignMotionPanel.h"
#include "Widgets/Docking/SDockTab.h"
#include "WorkspaceMenuStructure.h"
#include "WorkspaceMenuStructureModule.h"

#define LOCTEXT_NAMESPACE "SovereignMotionEditor"

namespace
{
	const FName StudioTabName(TEXT("SovereignMotionStudio"));
}

class FSovereignMotionEditorModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		// Appears under Tools in the level editor's menu bar.
		FGlobalTabmanager::Get()->RegisterNomadTabSpawner(StudioTabName,
				FOnSpawnTab::CreateRaw(this, &FSovereignMotionEditorModule::SpawnStudioTab))
			.SetDisplayName(LOCTEXT("StudioTabTitle", "Sovereign Motion Studio"))
			.SetTooltipText(LOCTEXT("StudioTabTip", "Generate and bake contextual motion."))
			.SetGroup(WorkspaceMenu::GetMenuStructure().GetToolsCategory());
	}

	virtual void ShutdownModule() override
	{
		if (FSlateApplication::IsInitialized())
		{
			FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(StudioTabName);
		}
	}

private:
	TSharedRef<SDockTab> SpawnStudioTab(const FSpawnTabArgs&)
	{
		return SNew(SDockTab)
			.TabRole(ETabRole::NomadTab)
			[
				SNew(SSovereignMotionPanel)
			];
	}
};

IMPLEMENT_MODULE(FSovereignMotionEditorModule, SovereignMotionEditor)

#undef LOCTEXT_NAMESPACE
