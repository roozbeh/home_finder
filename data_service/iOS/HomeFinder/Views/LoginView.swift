import SwiftUI
import AuthenticationServices
import GoogleSignIn

private let brandBlue = Color(red: 0.035, green: 0.165, blue: 0.337)

struct LoginView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        ZStack(alignment: .topTrailing) {
            brandBlue.ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()

                // Logo + headline
                VStack(spacing: 12) {
                    Text("🏠")
                        .font(.system(size: 64))

                    Text("roozbeh.realtor")
                        .font(.system(size: 32, weight: .bold))
                        .foregroundColor(.white)

                    Text("Bay Area Home Finder")
                        .font(.system(size: 17))
                        .foregroundColor(.white.opacity(0.75))

                    Text("DRE#: 02225608")
                        .font(.system(size: 13))
                        .foregroundColor(.white.opacity(0.5))
                        .padding(.top, 2)
                }

                Spacer()

                // Sign-in buttons
                VStack(spacing: 14) {
                    SignInWithAppleButton(.signIn) { request in
                        request.requestedScopes = [.fullName, .email]
                    } onCompletion: { result in
                        switch result {
                        case .success(let auth):
                            if let cred = auth.credential as? ASAuthorizationAppleIDCredential {
                                vm.signInWithApple(credential: cred)
                            }
                        case .failure(let error):
                            vm.errorMessage = error.localizedDescription
                        }
                    }
                    .signInWithAppleButtonStyle(.white)
                    .frame(height: 54)
                    .clipShape(RoundedRectangle(cornerRadius: 14))

                    Button(action: {
                        guard let windowScene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
                              let root = windowScene.windows.first?.rootViewController else { return }
                        GIDSignIn.sharedInstance.signIn(withPresenting: root) { result, error in
                            guard let result, error == nil,
                                  let idToken = result.user.idToken?.tokenString else {
                                if let error { vm.errorMessage = error.localizedDescription }
                                return
                            }
                            let name  = result.user.profile?.name  ?? ""
                            let email = result.user.profile?.email ?? ""
                            vm.signInWithGoogle(idToken: idToken, name: name, email: email)
                        }
                    }) {
                        HStack(spacing: 10) {
                            // Google "G" logo using colored letters
                            Text("G")
                                .font(.system(size: 20, weight: .bold))
                                .foregroundColor(Color(red: 0.26, green: 0.52, blue: 0.96))
                            Text("Sign in with Google")
                                .font(.system(size: 17, weight: .semibold))
                                .foregroundColor(Color(red: 0.13, green: 0.13, blue: 0.13))
                        }
                        .frame(maxWidth: .infinity)
                        .frame(height: 54)
                        .background(Color.white)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }

                    if let error = vm.errorMessage {
                        Text(error)
                            .font(.system(size: 13))
                            .foregroundColor(Color(red: 1, green: 0.6, blue: 0.6))
                            .multilineTextAlignment(.center)
                    }
                }
                .padding(.horizontal, 32)
                .padding(.bottom, 56)
            }

            // Dismiss button
            Button(action: { vm.showingLogin = false }) {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(.white.opacity(0.7))
                    .padding(12)
                    .background(Color.white.opacity(0.15))
                    .clipShape(Circle())
            }
            .padding(.top, 56)
            .padding(.trailing, 20)
        }
        .onAppear { vm.errorMessage = nil }
    }
}
