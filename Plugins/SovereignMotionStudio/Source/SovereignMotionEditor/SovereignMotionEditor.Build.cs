using UnrealBuildTool;

public class SovereignMotionEditor : ModuleRules
{
	public SovereignMotionEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"SovereignMotionCore",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"AssetRegistry",
			"InputCore",
			"PropertyEditor",
			"Slate",
			"SlateCore",
			"UnrealEd",
			"WorkspaceMenuStructure",
		});
	}
}
