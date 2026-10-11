#include "SovereignMotionBakeLibrary.h"

#include "Animation/AnimData/CurveIdentifier.h"
#include "Animation/AnimData/IAnimationDataController.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "PackageTools.h"
#include "SovereignMotionJson.h"

#define LOCTEXT_NAMESPACE "SovereignMotionBake"

namespace
{
	const FName LeftContactCurve(TEXT("FootContact_L"));
	const FName RightContactCurve(TEXT("FootContact_R"));

	/** Find a package name that doesn't exist yet: Base, Base_1, Base_2, ... */
	FString MakeUniquePackageName(const FString& BasePackageName)
	{
		FString Candidate = BasePackageName;
		for (int32 Suffix = 1; FPackageName::DoesPackageExist(Candidate) || FindPackage(nullptr, *Candidate); ++Suffix)
		{
			Candidate = FString::Printf(TEXT("%s_%d"), *BasePackageName, Suffix);
		}
		return Candidate;
	}

	void AddContactCurve(IAnimationDataController& Controller, const FName& Name, const TArray<bool>& Contacts, int32 Fps)
	{
		if (Contacts.Num() == 0)
		{
			return;
		}
		const FAnimationCurveIdentifier CurveId(Name, ERawCurveTrackTypes::RCT_Float);
		Controller.AddCurve(CurveId);

		// One stepped key per change keeps the curve small and exact.
		TArray<FRichCurveKey> Keys;
		for (int32 Frame = 0; Frame < Contacts.Num(); ++Frame)
		{
			if (Frame == 0 || Contacts[Frame] != Contacts[Frame - 1])
			{
				FRichCurveKey Key(float(Frame) / float(Fps), Contacts[Frame] ? 1.f : 0.f);
				Key.InterpMode = RCIM_Constant;
				Keys.Add(Key);
			}
		}
		Controller.SetCurveKeys(CurveId, Keys);
	}
}

UAnimSequence* USovereignMotionBakeLibrary::BakeResult(const FSovereignMotionResult& Result, USkeleton* Skeleton,
	const FString& PackagePath, const FString& AssetName, bool bRelativeToStart, FString& OutMessage)
{
	OutMessage.Reset();
	if (!Skeleton)
	{
		OutMessage = TEXT("No source skeleton selected.");
		return nullptr;
	}
	if (Result.Frames.Num() < 2 || Result.Fps <= 0)
	{
		OutMessage = TEXT("Result has fewer than two frames or an invalid frame rate.");
		return nullptr;
	}

	// Every backend joint must exist on the skeleton, or the clip would silently lose motion.
	const FReferenceSkeleton& RefSkeleton = Skeleton->GetReferenceSkeleton();
	const int32 NumBones = RefSkeleton.GetNum();
	TArray<int32> BoneToJoint;
	BoneToJoint.Init(INDEX_NONE, NumBones);
	TArray<FString> Missing;
	for (int32 JointIndex = 0; JointIndex < Result.Joints.Num(); ++JointIndex)
	{
		const int32 BoneIndex = RefSkeleton.FindBoneIndex(Result.Joints[JointIndex].Name);
		if (BoneIndex == INDEX_NONE)
		{
			Missing.Add(Result.Joints[JointIndex].Name.ToString());
		}
		else
		{
			BoneToJoint[BoneIndex] = JointIndex;
		}
	}
	if (Missing.Num() > 0)
	{
		OutMessage = FString::Printf(TEXT("Skeleton %s is missing %d backend joint(s): %s"),
			*Skeleton->GetName(), Missing.Num(), *FString::Join(Missing, TEXT(", ")));
		return nullptr;
	}

	// Reference-pose component-space rotations of the target skeleton (parents precede children).
	const TArray<FTransform>& RefPose = RefSkeleton.GetRefBonePose();
	TArray<FQuat> RefComponent;
	RefComponent.SetNum(NumBones);
	for (int32 Bone = 0; Bone < NumBones; ++Bone)
	{
		const int32 Parent = RefSkeleton.GetParentIndex(Bone);
		RefComponent[Bone] = Parent == INDEX_NONE ? RefPose[Bone].GetRotation() : RefComponent[Parent] * RefPose[Bone].GetRotation();
	}

	const FString BaseName = AssetName.IsEmpty() ? FString::Printf(TEXT("AS_%s"), *Result.RequestId) : AssetName;
	const FString PackageName = MakeUniquePackageName(UPackageTools::SanitizePackageName(PackagePath / BaseName));
	FText PackageError;
	if (!FPackageName::IsValidLongPackageName(PackageName, false, &PackageError))
	{
		OutMessage = FString::Printf(TEXT("Invalid asset path %s: %s"), *PackageName, *PackageError.ToString());
		return nullptr;
	}

	UPackage* Package = CreatePackage(*PackageName);
	Package->FullyLoad();
	UAnimSequence* Sequence = NewObject<UAnimSequence>(
		Package, FName(*FPackageName::GetShortName(PackageName)), RF_Public | RF_Standalone | RF_Transactional);
	Sequence->SetSkeleton(Skeleton);

	const int32 NumKeys = Result.Frames.Num();
	IAnimationDataController& Controller = Sequence->GetController();
	Controller.OpenBracket(LOCTEXT("BakeBracket", "Bake Sovereign Motion"));
	Controller.InitializeModel();
	Controller.SetFrameRate(FFrameRate(Result.Fps, 1));
	Controller.SetNumberOfFrames(FFrameNumber(NumKeys - 1));

	// Result rotations are relative to a bind pose with identity rotations. Transfer them in
	// component space so the target's own bind orientations (bone rolls from DCC tools) and
	// proportions are respected:
	//   TargetComponent = ResultComponent * TargetRefComponent
	//   TargetLocal     = inverse(TargetParentComponent) * TargetComponent
	TArray<TArray<FQuat4f>> BoneRotations;
	BoneRotations.SetNum(NumBones);
	TArray<FVector3f> RootPositions;

	// Optionally re-express root motion relative to the first frame: undo the start position and
	// the start heading (yaw only), so the clip begins at the origin with the mesh facing its own forward.
	FQuat StartYawInverse = FQuat::Identity;
	FVector StartTranslation = FVector::ZeroVector;
	if (bRelativeToStart)
	{
		const int32 RootJoint = 0;
		const float StartYaw = Result.Frames[0].Rotations[RootJoint].Rotator().Yaw;
		StartYawInverse = FQuat(FVector::UpVector, FMath::DegreesToRadians(-StartYaw));
		StartTranslation = Result.Frames[0].RootTranslation;
	}
	TArray<FQuat> ResultComponent;
	TArray<FQuat> TargetComponent;
	ResultComponent.SetNum(Result.Joints.Num());
	TargetComponent.SetNum(NumBones);
	for (const FSovereignMotionFrame& Frame : Result.Frames)
	{
		for (int32 JointIndex = 0; JointIndex < Result.Joints.Num(); ++JointIndex)
		{
			const int32 Parent = Result.Joints[JointIndex].Parent;
			ResultComponent[JointIndex] = Parent == INDEX_NONE
				? StartYawInverse * Frame.Rotations[JointIndex]
				: ResultComponent[Parent] * Frame.Rotations[JointIndex];
		}
		for (int32 Bone = 0; Bone < NumBones; ++Bone)
		{
			const int32 Parent = RefSkeleton.GetParentIndex(Bone);
			const FQuat ParentComponent = Parent == INDEX_NONE ? FQuat::Identity : TargetComponent[Parent];
			const int32 JointIndex = BoneToJoint[Bone];
			if (JointIndex == INDEX_NONE)
			{
				// Bones the backend doesn't drive follow their parent in reference pose.
				TargetComponent[Bone] = ParentComponent * RefPose[Bone].GetRotation();
				continue;
			}
			TargetComponent[Bone] = ResultComponent[JointIndex] * RefComponent[Bone];
			BoneRotations[Bone].Add(FQuat4f((ParentComponent.Inverse() * TargetComponent[Bone]).GetNormalized()));
			if (Result.Joints[JointIndex].Parent == INDEX_NONE)
			{
				const FVector RootMotion = StartYawInverse.RotateVector(Frame.RootTranslation - StartTranslation);
				RootPositions.Add(FVector3f(RootMotion + Result.Joints[JointIndex].Offset));
			}
		}
	}

	for (int32 Bone = 0; Bone < NumBones; ++Bone)
	{
		const int32 JointIndex = BoneToJoint[Bone];
		if (JointIndex == INDEX_NONE)
		{
			continue;
		}
		// Only the backend root moves; other bones keep the target's own bind offsets (its proportions).
		const bool bIsRoot = Result.Joints[JointIndex].Parent == INDEX_NONE;
		TArray<FVector3f> Positions;
		if (bIsRoot)
		{
			Positions = RootPositions;
		}
		else
		{
			Positions.Init(FVector3f(RefPose[Bone].GetTranslation()), NumKeys);
		}
		TArray<FVector3f> Scales;
		Scales.Init(FVector3f::OneVector, NumKeys);
		const FName BoneName = RefSkeleton.GetBoneName(Bone);
		Controller.AddBoneCurve(BoneName);
		Controller.SetBoneTrackKeys(BoneName, Positions, BoneRotations[Bone], Scales);
	}

	AddContactCurve(Controller, LeftContactCurve, Result.LeftFootContacts, Result.Fps);
	AddContactCurve(Controller, RightContactCurve, Result.RightFootContacts, Result.Fps);

	Controller.NotifyPopulated();
	Controller.CloseBracket();

	Sequence->bEnableRootMotion = bRelativeToStart;
	Sequence->PostEditChange();
	Package->MarkPackageDirty();
	FAssetRegistryModule::AssetCreated(Sequence);

	OutMessage = FString::Printf(TEXT("Baked %s (%d frames, %.2fs). Save the asset to keep it."),
		*PackageName, NumKeys, Result.GetDurationSeconds());
	return Sequence;
}

bool USovereignMotionBakeLibrary::LoadResultFromFile(const FString& FilePath, FSovereignMotionResult& OutResult, FString& OutError)
{
	FString Json;
	if (!FFileHelper::LoadFileToString(Json, *FilePath))
	{
		OutError = FString::Printf(TEXT("Could not read %s"), *FilePath);
		return false;
	}
	return SovereignMotionJson::ResultFromJson(Json, OutResult, OutError);
}

#undef LOCTEXT_NAMESPACE
