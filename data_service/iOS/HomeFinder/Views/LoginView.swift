import SwiftUI
import AuthenticationServices

private let brandBlue = Color(red: 0.035, green: 0.165, blue: 0.337)

struct LoginView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        ZStack {
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
        }
    }
}
