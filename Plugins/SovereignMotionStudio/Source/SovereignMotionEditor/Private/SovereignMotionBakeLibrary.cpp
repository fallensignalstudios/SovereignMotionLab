#include "SovereignMotionBakeLibrary.h"

#include "Animation/AnimCurveTypes.h"
#include "Animation/AnimData/CurveIdentifier.h"
#include "Animation/AnimData/IAnimationDataController.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "PackageTools.h"
#include "UObject/Package.h"
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

	void AddContactCurve(IAnimationDataController& Controller, const FName& Name, const TArray<bool>& Contacts,
		int32 Fps, bool bTransact)
	{
		if (Contacts.Num() == 0)
		{
			return;
		}
		const FAnimationCurveIdentifier CurveId(Name, ERawCurveTrackTypes::RCT_Float);
		Controller.AddCurve(CurveId, AACF_Editable, bTransact);

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
		Controller.SetCurveKeys(CurveId, Keys, bTransact);
	}

	/** Map every result joint to a bone on the skeleton. Fails if any joint is missing. */
	bool MapJoints(const FSovereignMotionResult& Result, const USkeleton* Skeleton, TArray<int32>& OutBoneToJoint, FString& OutError)
	{
		if (!Skeleton)
		{
			OutError = TEXT("No skeleton selected.");
			return false;
		}
		if (Result.Frames.Num() < 2 || Result.Fps <= 0)
		{
			OutError = TEXT("Result has fewer than two frames or an invalid frame rate.");
			return false;
		}

		// Every backend joint must exist on the skeleton, or the clip would silently lose motion.
		const FReferenceSkeleton& RefSkeleton = Skeleton->GetReferenceSkeleton();
		OutBoneToJoint.Init(INDEX_NONE, RefSkeleton.GetNum());
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
				OutBoneToJoint[BoneIndex] = JointIndex;
			}
		}
		if (Missing.Num() > 0)
		{
			OutError = FString::Printf(TEXT("Skeleton %s is missing %d backend joint(s): %s"),
				*Skeleton->GetName(), Missing.Num(), *FString::Join(Missing, TEXT(", ")));
			return false;
		}
		return true;
	}

	/** Resolve IK bone -> source bone names to indices, skipping pairs the skeleton doesn't have. */
	TArray<TPair<int32, int32>> ResolveIKBones(const FReferenceSkeleton& RefSkeleton, const TMap<FName, FName>& IKBoneSources)
	{
		TArray<TPair<int32, int32>> Pairs;
		for (const TPair<FName, FName>& Entry : IKBoneSources)
		{
			const int32 IKBone = RefSkeleton.FindBoneIndex(Entry.Key);
			const int32 Source = RefSkeleton.FindBoneIndex(Entry.Value);
			if (IKBone != INDEX_NONE && Source != INDEX_NONE && IKBone != Source)
			{
				Pairs.Emplace(IKBone, Source);
			}
		}
		return Pairs;
	}

	/** Write the clip's keys into an empty sequence that already has its skeleton set. */
	void WriteKeys(UAnimSequence& Sequence, const FSovereignMotionResult& Result, const TArray<int32>& BoneToJoint,
		const TMap<FName, FName>& IKBoneSources, bool bRelativeToStart, bool bTransact)
	{
		const FReferenceSkeleton& RefSkeleton = Sequence.GetSkeleton()->GetReferenceSkeleton();
		const int32 NumBones = RefSkeleton.GetNum();
		const TArray<FTransform>& RefPose = RefSkeleton.GetRefBonePose();

		// Reference-pose component-space rotations of the target skeleton (parents precede children).
		TArray<FQuat> RefComponent;
		RefComponent.SetNum(NumBones);
		for (int32 Bone = 0; Bone < NumBones; ++Bone)
		{
			const int32 Parent = RefSkeleton.GetParentIndex(Bone);
			RefComponent[Bone] = Parent == INDEX_NONE ? RefPose[Bone].GetRotation() : RefComponent[Parent] * RefPose[Bone].GetRotation();
		}

		// IK helper bones (ik_foot_l, ik_hand_gun, ...) are snapped onto their source bones each frame.
		const TArray<TPair<int32, int32>> IKBones = ResolveIKBones(RefSkeleton, IKBoneSources);
		TArray<int32> IKSource;
		IKSource.Init(INDEX_NONE, NumBones);
		for (const TPair<int32, int32>& Pair : IKBones)
		{
			IKSource[Pair.Key] = Pair.Value;
		}

		// Bones that get tracks: everything the result drives, plus the IK bones.
		TArray<bool> bKeyed;
		bKeyed.Init(false, NumBones);
		for (int32 Bone = 0; Bone < NumBones; ++Bone)
		{
			bKeyed[Bone] = BoneToJoint[Bone] != INDEX_NONE || IKSource[Bone] != INDEX_NONE;
		}

		const int32 NumKeys = Result.Frames.Num();
		IAnimationDataController& Controller = Sequence.GetController();
		Controller.OpenBracket(LOCTEXT("BakeBracket", "Bake Sovereign Motion"), bTransact);
		Controller.InitializeModel();
		Controller.SetFrameRate(FFrameRate(Result.Fps, 1), bTransact);
		Controller.SetNumberOfFrames(FFrameNumber(NumKeys - 1), bTransact);

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

		TArray<TArray<FVector3f>> TrackPositions;
		TArray<TArray<FQuat4f>> TrackRotations;
		TrackPositions.SetNum(NumBones);
		TrackRotations.SetNum(NumBones);
		TArray<FQuat> ResultComponent;
		TArray<FQuat> TargetComponentRotation;
		TArray<FTransform> Local;
		TArray<FTransform> Component;
		ResultComponent.SetNum(Result.Joints.Num());
		TargetComponentRotation.SetNum(NumBones);
		Local.SetNum(NumBones);
		Component.SetNum(NumBones);

		for (const FSovereignMotionFrame& Frame : Result.Frames)
		{
			for (int32 JointIndex = 0; JointIndex < Result.Joints.Num(); ++JointIndex)
			{
				const int32 Parent = Result.Joints[JointIndex].Parent;
				ResultComponent[JointIndex] = Parent == INDEX_NONE
					? StartYawInverse * Frame.Rotations[JointIndex]
					: ResultComponent[Parent] * Frame.Rotations[JointIndex];
			}

			// Pass 1: the local pose. Result rotations are relative to a bind pose with identity
			// rotations, so transfer them in component space to respect the target's own bind
			// orientations (bone rolls from DCC tools):
			//   TargetComponent = ResultComponent * TargetRefComponent
			//   TargetLocal     = inverse(TargetParentComponent) * TargetComponent
			// Only the result's root moves; other bones keep the target's bind offsets (its proportions).
			for (int32 Bone = 0; Bone < NumBones; ++Bone)
			{
				const int32 Parent = RefSkeleton.GetParentIndex(Bone);
				const FQuat ParentRotation = Parent == INDEX_NONE ? FQuat::Identity : TargetComponentRotation[Parent];
				const int32 JointIndex = BoneToJoint[Bone];
				Local[Bone] = RefPose[Bone];
				if (JointIndex == INDEX_NONE)
				{
					TargetComponentRotation[Bone] = ParentRotation * RefPose[Bone].GetRotation();
				}
				else
				{
					TargetComponentRotation[Bone] = ResultComponent[JointIndex] * RefComponent[Bone];
					Local[Bone].SetRotation((ParentRotation.Inverse() * TargetComponentRotation[Bone]).GetNormalized());
					if (Result.Joints[JointIndex].Parent == INDEX_NONE)
					{
						const FVector RootMotion = StartYawInverse.RotateVector(Frame.RootTranslation - StartTranslation);
						Local[Bone].SetTranslation(RootMotion + Result.Joints[JointIndex].Offset);
					}
				}
				Component[Bone] = Parent == INDEX_NONE ? Local[Bone] : Local[Bone] * Component[Parent];
			}

			// Pass 2: snap IK bones onto their sources in component space, then re-derive their locals
			// (and the component transforms of anything parented under them, e.g. ik_hand_l under ik_hand_gun).
			if (IKBones.Num() > 0)
			{
				const TArray<FTransform> Posed = Component;
				for (int32 Bone = 0; Bone < NumBones; ++Bone)
				{
					const int32 Parent = RefSkeleton.GetParentIndex(Bone);
					if (IKSource[Bone] != INDEX_NONE)
					{
						Component[Bone] = Posed[IKSource[Bone]];
						Local[Bone] = Parent == INDEX_NONE ? Component[Bone] : Component[Bone].GetRelativeTransform(Component[Parent]);
					}
					else if (Parent != INDEX_NONE)
					{
						Component[Bone] = Local[Bone] * Component[Parent];
					}
				}
			}

			for (int32 Bone = 0; Bone < NumBones; ++Bone)
			{
				if (bKeyed[Bone])
				{
					TrackPositions[Bone].Add(FVector3f(Local[Bone].GetTranslation()));
					TrackRotations[Bone].Add(FQuat4f(Local[Bone].GetRotation().GetNormalized()));
				}
			}
		}

		TArray<FVector3f> Scales;
		Scales.Init(FVector3f::OneVector, NumKeys);
		for (int32 Bone = 0; Bone < NumBones; ++Bone)
		{
			if (!bKeyed[Bone])
			{
				continue;
			}
			const FName BoneName = RefSkeleton.GetBoneName(Bone);
			Controller.AddBoneCurve(BoneName, bTransact);
			Controller.SetBoneTrackKeys(BoneName, TrackPositions[Bone], TrackRotations[Bone], Scales, bTransact);
		}

		AddContactCurve(Controller, LeftContactCurve, Result.LeftFootContacts, Result.Fps, bTransact);
		AddContactCurve(Controller, RightContactCurve, Result.RightFootContacts, Result.Fps, bTransact);

		Controller.NotifyPopulated();
		Controller.CloseBracket(bTransact);

		Sequence.bEnableRootMotion = bRelativeToStart;
	}
}

TMap<FName, FName> USovereignMotionBakeLibrary::DefaultIKBoneSources()
{
	return SovereignMotionBake::DefaultIKBoneSources();
}

UAnimSequence* USovereignMotionBakeLibrary::BakeResult(const FSovereignMotionResult& Result, USkeleton* Skeleton,
	const FString& PackagePath, const FString& AssetName, bool bRelativeToStart,
	const TMap<FName, FName>& IKBoneSources, FString& OutMessage)
{
	OutMessage.Reset();
	TArray<int32> BoneToJoint;
	if (!MapJoints(Result, Skeleton, BoneToJoint, OutMessage))
	{
		return nullptr;
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
	WriteKeys(*Sequence, Result, BoneToJoint, IKBoneSources, bRelativeToStart, /*bTransact=*/ true);

	Sequence->PostEditChange();
	Package->MarkPackageDirty();
	FAssetRegistryModule::AssetCreated(Sequence);

	OutMessage = FString::Printf(TEXT("Baked %s (%d frames, %.2fs). Save the asset to keep it."),
		*PackageName, Result.Frames.Num(), Result.GetDurationSeconds());
	return Sequence;
}

UAnimSequence* USovereignMotionBakeLibrary::BuildPreviewSequence(const FSovereignMotionResult& Result, USkeleton* Skeleton,
	const TMap<FName, FName>& IKBoneSources, FString& OutError)
{
	TArray<int32> BoneToJoint;
	if (!MapJoints(Result, Skeleton, BoneToJoint, OutError))
	{
		return nullptr;
	}
	// Transient and untransacted: never saved, never in the undo history. Level-space root motion,
	// so a mesh at the world origin plays the clip exactly along the authored path.
	UAnimSequence* Sequence = NewObject<UAnimSequence>(GetTransientPackage(), NAME_None, RF_Transient);
	Sequence->SetSkeleton(Skeleton);
	WriteKeys(*Sequence, Result, BoneToJoint, IKBoneSources, /*bRelativeToStart=*/ false, /*bTransact=*/ false);
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
