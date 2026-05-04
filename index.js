exports.handler = async (event) => {
  try {
    const body = JSON.parse(event.body);

    const { email, password } = body;

    // Validación básica
    if (!email || !password) {
      return {
        statusCode: 400,
        body: JSON.stringify({
          message: "Email y password son requeridos"
        })
      };
    }

    // Simulación de usuario (luego esto será DynamoDB)
    if (email === "admin@test.com" && password === "123456") {
      return {
        statusCode: 200,
        body: JSON.stringify({
          message: "Login exitoso",
          token: "fake-jwt-token"
        })
      };
    }

    return {
      statusCode: 401,
      body: JSON.stringify({
        message: "Credenciales inválidas"
      })
    };

  } catch (error) {
    return {
      statusCode: 500,
      body: JSON.stringify({
        message: "Error interno",
        error: error.message
      })
    };
  }
};
